"""Exercise the pinned inactive strict-thinking mask optimization without a GPU.

Run with ``SGLANG_SOURCE_ROOT`` pointing at the installed ``sglang`` package.
The test extracts the changed methods from the pinned source, so it exercises
the patched control flow without importing the full serving stack.
"""
from collections import namedtuple
import ast
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import typing
import unittest

import torch


ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "docker/strict-thinking/sglang-db272201.patch"


def _package_root():
    configured = os.environ.get("SGLANG_SOURCE_ROOT")
    if configured:
        return Path(configured).resolve()
    package = importlib.util.find_spec("sglang")
    if package is None or package.origin is None:
        raise RuntimeError("Set SGLANG_SOURCE_ROOT or install sglang")
    return Path(package.origin).resolve().parent


SOURCE_ROOT = _package_root()
GrammarRow = namedtuple("GrammarRow", ["row", "grammar"])


class TokenSequenceMatcher:
    """Minimal equivalent of SGLang's matcher for the tested token sequences."""

    def __init__(self, pattern):
        self.pattern = tuple(pattern)

    def __len__(self):
        return len(self.pattern)

    def advance(self, matched, token):
        candidate = self.pattern[:matched] + (token,)
        for size in range(min(len(candidate), len(self.pattern)), 0, -1):
            if tuple(candidate[-size:]) == self.pattern[:size]:
                return size
        return 0


def _load_class(source_path, class_name, namespace):
    tree = ast.parse(source_path.read_text())
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.ClassDef) and item.name == class_name
    )
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    module = ast.Module(body=[future, node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(source_path), "exec"), namespace)
    return namespace[class_name]


def _load_function(source_path, function_name, namespace):
    tree = ast.parse(source_path.read_text())
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == function_name
    )
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    module = ast.Module(body=[future, node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(source_path), "exec"), namespace)
    return namespace[function_name]


def _load_method(source_path, class_name, function_name, namespace):
    tree = ast.parse(source_path.read_text())
    owner = next(
        item
        for item in tree.body
        if isinstance(item, ast.ClassDef) and item.name == class_name
    )
    node = next(
        item
        for item in owner.body
        if isinstance(item, ast.FunctionDef) and item.name == function_name
    )
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    module = ast.Module(body=[future, node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(source_path), "exec"), namespace)
    return namespace[function_name]


class BaseGrammarObject:
    def __init__(self):
        self._finished = False

    @property
    def finished(self):
        return self._finished

    @finished.setter
    def finished(self, value):
        self._finished = value

    def is_terminated(self):
        return False


def _load_base_object():
    namespace = {
        "BaseGrammarObject": BaseGrammarObject,
        "GrammarRow": GrammarRow,
        "List": typing.List,
        "Optional": typing.Optional,
        "Tuple": typing.Tuple,
        "torch": torch,
    }
    return _load_class(
        SOURCE_ROOT / "srt/constrained/base_grammar_backend.py",
        "BaseGrammarObject",
        namespace,
    )


def _load_reasoner_object():
    namespace = {
        "BaseGrammarObject": _load_base_object(),
        "TokenSequenceMatcher": TokenSequenceMatcher,
        "List": typing.List,
        "Optional": typing.Optional,
        "Sequence": typing.Sequence,
        "Union": typing.Union,
        "torch": torch,
    }
    return _load_class(
        SOURCE_ROOT / "srt/constrained/reasoner_grammar_backend.py",
        "ReasonerGrammarObject",
        namespace,
    )


class InnerGrammar(BaseGrammarObject):
    def __init__(self, terminated=False):
        super().__init__()
        self.terminated = terminated

    def is_terminated(self):
        return self.terminated

    def allocate_vocab_mask(self, vocab_size, batch_size, device):
        return torch.full((batch_size, (vocab_size + 31) // 32), -1, dtype=torch.int32)

    def move_vocab_mask(self, vocab_mask, device):
        return vocab_mask

    def apply_vocab_mask(self, logits, vocab_mask):
        return None

    def accept_token(self, token):
        return None

    def rollback(self, count):
        return None


class ReasonerMaskStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Base = _load_base_object()
        cls.Reasoner = _load_reasoner_object()

    def make(self, *, grammar=None, excluded=None, budget=-1, filter_tokens=False):
        return self.Reasoner(
            grammar=grammar,
            think_end_ids=[7],
            think_excluded_token_ids=excluded,
            max_think_tokens=budget,
            enable_token_filter=filter_tokens,
        )

    def test_active_excluded_tokens_keep_mask(self):
        obj = self.make(excluded=[3], filter_tokens=True)
        obj.maybe_init_reasoning(True)
        self.assertTrue(obj.requires_vocab_mask())
        self.assertTrue(obj.may_require_vocab_mask())

    def test_budget_edge_keeps_force_end_mask(self):
        obj = self.make(budget=2, filter_tokens=True)
        obj.maybe_init_reasoning(True)
        obj.accept_token(10)
        obj.accept_token(11)
        self.assertFalse(obj._can_think_more())
        self.assertTrue(obj.requires_vocab_mask())

    def test_no_inner_grammar_generation_skips_mask(self):
        obj = self.make(filter_tokens=True)
        obj.maybe_init_reasoning(True)
        obj.accept_token(7)
        self.assertTrue(obj._is_generation())
        self.assertFalse(obj.requires_vocab_mask())
        self.assertFalse(obj.may_require_vocab_mask())

    def test_constrained_inner_grammar_generation_keeps_mask(self):
        obj = self.make(grammar=InnerGrammar(), filter_tokens=True)
        obj.maybe_init_reasoning(True)
        obj.accept_token(7)
        self.assertTrue(obj.requires_vocab_mask())
        self.assertTrue(obj.may_require_vocab_mask())

    def test_thinking_disabled_skips_mask(self):
        obj = self.make(grammar=None, filter_tokens=True)
        obj.maybe_init_reasoning(False)
        self.assertFalse(obj.requires_vocab_mask())
        self.assertFalse(obj.may_require_vocab_mask())

    def test_speculative_transition_never_uses_initial_state_only(self):
        obj = self.make(grammar=InnerGrammar(), filter_tokens=False)
        obj.maybe_init_reasoning(True)
        self.assertFalse(obj.requires_vocab_mask())
        self.assertTrue(obj.may_require_vocab_mask())
        obj.accept_token(7)
        self.assertTrue(obj.requires_vocab_mask())
        obj.rollback(1)
        self.assertTrue(obj._is_thinking())
        self.assertFalse(obj.requires_vocab_mask())
        self.assertTrue(obj.may_require_vocab_mask())


class SamplingBatchMaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        namespace = {"GrammarRow": GrammarRow}

        class GrammarMask:
            def __init__(self, grammar, vocab_mask):
                self.grammar = grammar
                self.vocab_mask = vocab_mask

        namespace["GrammarMask"] = GrammarMask
        cls.update = staticmethod(
            _load_method(
                SOURCE_ROOT / "srt/sampling/sampling_batch_info.py",
                "SamplingBatchInfo",
                "update_regex_vocab_mask",
                namespace,
            )
        )

    def make_batch(self, grammars):
        return SimpleNamespace(
            grammars=grammars,
            grammar_mask="sentinel",
            vocab_size=64,
            device="cpu",
            temperatures=[0],
        )

    def test_inactive_rows_skip_allocation(self):
        grammar = SimpleNamespace(
            finished=False,
            is_terminated=lambda: False,
            requires_vocab_mask=lambda: False,
        )
        batch = self.make_batch([grammar])
        self.update(batch)
        self.assertIsNone(batch.grammar_mask)

    def test_mixed_batch_fills_only_active_rows(self):
        calls = {}

        class Grammar:
            def __init__(self, active):
                self.active = active
                self.finished = False

            def is_terminated(self):
                return False

            def requires_vocab_mask(self):
                return self.active

            def allocate_vocab_mask(self, **kwargs):
                calls["allocate"] = kwargs
                return "mask"

            def fill_vocab_mask_batched(self, entries, vocab_mask):
                calls["entries"] = entries
                calls["mask"] = vocab_mask

            def move_vocab_mask(self, vocab_mask, device):
                calls["device"] = device
                return vocab_mask

        inactive = Grammar(False)
        active = Grammar(True)
        batch = self.make_batch([inactive, active])
        self.update(batch)
        self.assertEqual([(entry.row, entry.grammar) for entry in calls["entries"]], [(1, active)])
        self.assertEqual(calls["mask"], "mask")
        self.assertEqual(batch.grammar_mask.grammar, active)


class SpeculativeMaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Reasoner = _load_reasoner_object()
        cls.traverse_calls = []

        def traverse_tree(*args, **kwargs):
            cls.traverse_calls.append((args, kwargs))

        namespace = {
            "torch": torch,
            "List": list,
            "Optional": SimpleNamespace,
            "Tuple": tuple,
            "time": SimpleNamespace(perf_counter=lambda: 0.0),
            "logger": SimpleNamespace(warning=lambda *args, **kwargs: None),
            "traverse_tree": traverse_tree,
            "TREE_TRAVERSE_TIME_THRESHOLD": 1.0,
        }
        cls.generate = staticmethod(
            _load_function(
                SOURCE_ROOT / "srt/speculative/spec_utils.py",
                "generate_token_bitmask",
                namespace,
            )
        )

    def setUp(self):
        self.traverse_calls.clear()
        self.retrieve = torch.zeros((1, 2), dtype=torch.int32)
        self.sibling = torch.full((1, 2), -1, dtype=torch.int32)
        self.tokens = torch.tensor([[100, 7]], dtype=torch.int64)

    def test_inactive_tree_skips_allocation(self):
        grammar = SimpleNamespace(
            may_require_vocab_mask=lambda: False,
            allocate_vocab_mask=lambda **kwargs: self.fail("inactive tree allocated"),
        )
        req = SimpleNamespace(grammar=grammar)
        mask, owner = self.generate(
            [req], self.retrieve, self.sibling, self.tokens, vocab_size=64
        )
        self.assertIsNone(mask)
        self.assertIsNone(owner)
        self.assertEqual(self.traverse_calls, [])

    def test_budget_crossing_with_empty_exclusions_keeps_mask(self):
        grammar = self.Reasoner(
            grammar=None,
            think_end_ids=[7],
            think_excluded_token_ids=[],
            max_think_tokens=2,
            enable_token_filter=True,
            allocate_vocab_mask_fn=lambda vocab_size, batch_size, device: torch.full(
                (batch_size, (vocab_size + 31) // 32), -1, dtype=torch.int32
            ),
            move_vocab_mask_fn=lambda mask, device: mask,
            apply_vocab_mask_fn=lambda logits, mask: None,
        )
        grammar.maybe_init_reasoning(True)
        self.assertFalse(grammar.requires_vocab_mask())
        self.assertTrue(grammar.may_require_vocab_mask())

        def traverse_tree(*args, **kwargs):
            self.traverse_calls.append((args, kwargs))
            grammar.accept_token(10)
            grammar.accept_token(11)
            self.assertFalse(grammar._can_think_more())
            self.assertTrue(grammar.requires_vocab_mask())
            grammar.rollback(2)
            self.assertTrue(grammar._is_thinking())
            self.assertEqual(grammar.tokens_in_think, 0)

        self.generate.__globals__["traverse_tree"] = traverse_tree
        mask, owner = self.generate(
            [SimpleNamespace(grammar=grammar)],
            self.retrieve,
            self.sibling,
            self.tokens,
            vocab_size=64,
        )
        self.assertIsNotNone(mask)
        self.assertIs(owner, grammar)
        self.assertEqual(len(self.traverse_calls), 1)

    def test_skipped_unconstrained_tree_commit_still_advances_fsm(self):
        grammar = self.Reasoner(
            grammar=None,
            think_end_ids=[7],
            think_excluded_token_ids=None,
            max_think_tokens=-1,
            enable_token_filter=False,
            allocate_vocab_mask_fn=lambda **kwargs: self.fail(
                "unconstrained tree allocated"
            ),
        )
        grammar.maybe_init_reasoning(True)
        self.assertFalse(grammar.may_require_vocab_mask())
        mask, owner = self.generate(
            [SimpleNamespace(grammar=grammar)],
            self.retrieve,
            self.sibling,
            self.tokens,
            vocab_size=64,
        )
        self.assertIsNone(mask)
        self.assertIsNone(owner)
        self.assertEqual(self.traverse_calls, [])
        self.assertIsNone(grammar.current_token)

        # The normal commit handler still advances the skipped request.
        grammar.accept_token(7)
        self.assertTrue(grammar._is_generation())
        self.assertEqual(grammar.current_token, 7)

    def test_transition_inside_tree_keeps_allocation_and_rolls_back(self):
        inner = InnerGrammar()
        grammar = self.Reasoner(
            grammar=inner,
            think_end_ids=[7],
            think_excluded_token_ids=None,
            max_think_tokens=-1,
            enable_token_filter=False,
            allocate_vocab_mask_fn=lambda **kwargs: self.fail(
                "inner grammar allocation should be used"
            ),
            move_vocab_mask_fn=lambda mask, device: mask,
            apply_vocab_mask_fn=lambda logits, mask: None,
        )
        grammar.maybe_init_reasoning(True)
        self.assertFalse(grammar.requires_vocab_mask())
        self.assertTrue(grammar.may_require_vocab_mask())

        def traverse_tree(*args, **kwargs):
            self.traverse_calls.append((args, kwargs))
            self.assertFalse(grammar.requires_vocab_mask())
            grammar.accept_token(7)
            self.assertTrue(grammar.requires_vocab_mask())
            grammar.rollback(1)

        # Replace the loader's stub with the transition-aware traversal.
        self.generate.__globals__["traverse_tree"] = traverse_tree
        req = SimpleNamespace(grammar=grammar)
        mask, owner = self.generate(
            [req], self.retrieve, self.sibling, self.tokens, vocab_size=64
        )
        self.assertIsNotNone(mask)
        self.assertIs(owner, grammar)
        self.assertEqual(mask.shape, (2, 2))
        self.assertEqual(len(self.traverse_calls), 1)
        self.assertTrue(grammar._is_thinking())
        self.assertFalse(grammar.requires_vocab_mask())


class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "install_thinking_mask_optimization",
            ROOT / "scripts/install_thinking_mask_optimization.py",
        )
        cls.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.installer)

    def _copy_original_tree(self, destination):
        package = destination / "sglang"
        for relative_path in self.installer.FILES:
            target = package / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE_ROOT / relative_path, target)

        state = self.installer.source_state(package)
        if set(state.values()) == {"patched"}:
            subprocess.run(
                ["git", "apply", "-R", "-p1", str(PATCH)],
                cwd=destination,
                check=True,
            )
        elif set(state.values()) != {"original"}:
            self.fail(f"Unexpected source state: {state}")
        return package

    def test_patch_hash_is_pinned(self):
        self.assertEqual(
            self.installer.sha256(PATCH), self.installer.PATCH_SHA256
        )

    def test_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = self._copy_original_tree(Path(tmp))
            self.assertTrue(self.installer.install(package, PATCH))
            self.assertFalse(self.installer.install(package, PATCH))
            self.assertEqual(
                set(self.installer.source_state(package).values()), {"patched"}
            )

    def test_install_succeeds_inside_parent_git_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "sglang"
            python_dir = repo / "python"
            python_dir.mkdir(parents=True)
            package = self._copy_original_tree(python_dir)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)

            self.assertTrue(self.installer.install(package, PATCH))
            self.assertEqual(
                set(self.installer.source_state(package).values()), {"patched"}
            )

    def test_source_drift_fails_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = self._copy_original_tree(Path(tmp))
            target = package / "srt/constrained/base_grammar_backend.py"
            target.write_bytes(target.read_bytes() + b"\n")
            with self.assertRaises(self.installer.SourceDriftError):
                self.installer.install(package, PATCH)


if __name__ == "__main__":
    unittest.main()
