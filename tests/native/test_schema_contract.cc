#include <gtest/gtest.h>
#include <picojson.h>
#include <xgrammar/compiler.h>
#include <xgrammar/grammar.h>
#include <xgrammar/matcher.h>
#include <xgrammar/tokenizer_info.h>

#include <filesystem>
#include <fstream>

namespace {
bool Accepts(
    const std::string& schema,
    const std::string& value,
    bool structural = false,
    bool whitespace = true,
    std::optional<int> indent = std::nullopt
) {
  std::vector<std::string> vocab;
  for (int c = 0; c < 128; ++c) vocab.emplace_back(1, static_cast<char>(c));
  vocab.emplace_back("<eos>");
  xgrammar::GrammarCompiler compiler(
      xgrammar::TokenizerInfo(vocab, xgrammar::VocabType::RAW, 129, std::vector<int32_t>{128}), 1
  );
  auto compiled = structural ? compiler.CompileStructuralTag(schema)
                             : compiler.CompileJSONSchema(schema, whitespace, indent);
  xgrammar::GrammarMatcher matcher(compiled);
  return matcher.AcceptString(value) && matcher.IsCompleted();
}

TEST(ServingSchemaContract, AnyOfPreservesSharedRequiredFields) {
  const std::string schema = R"({"type":"object","properties":{
    "required_text":{"type":"string"},"kind":{"type":"string"}},
    "required":["required_text","kind"],"additionalProperties":false,
    "anyOf":[{"properties":{"kind":{"const":"a"}}},
             {"properties":{"kind":{"const":"b"}}}]})";
  EXPECT_TRUE(Accepts(schema, R"({"required_text":"ok","kind":"a"})"));
  EXPECT_FALSE(Accepts(schema, R"({"kind":"a"})"));
  EXPECT_FALSE(Accepts(schema, R"({"required_text":7,"kind":"a"})"));
  EXPECT_FALSE(Accepts(schema, R"({"required_text":"ok","kind":"c"})"));
}

TEST(ServingSchemaContract, ContainsRequiresMatchingArrayMember) {
  const std::string schema = R"({"type":"array","items":{"enum":[1,2,3]},
    "contains":{"enum":[2]}})";
  EXPECT_TRUE(Accepts(schema, "[2]"));
  EXPECT_TRUE(Accepts(schema, "[1,3,2,1]"));
  EXPECT_FALSE(Accepts(schema, "[1,3]"));
  EXPECT_FALSE(Accepts(schema, "[]"));
}

TEST(ServingSchemaContract, MultipleContainsRequirementsAreConjoined) {
  const std::string schema = R"({"type":"array","items":{"enum":[1,2,3]},
    "contains":{"enum":[2]},"anyOf":[{"contains":{"enum":[1]}}]})";
  EXPECT_TRUE(Accepts(schema, "[1,2]"));
  EXPECT_TRUE(Accepts(schema, "[3,2,1,2]"));
  EXPECT_FALSE(Accepts(schema, "[1]"));
  EXPECT_FALSE(Accepts(schema, "[2]"));
}

TEST(ServingSchemaContract, AllOfDoesNotBecomeUnrestrictedJson) {
  const std::string schema = R"({"type":"integer","allOf":[
    {"minimum":2},{"maximum":4}]})";
  EXPECT_TRUE(Accepts(schema, "3"));
  EXPECT_FALSE(Accepts(schema, "1"));
  EXPECT_FALSE(Accepts(schema, "5"));
  EXPECT_FALSE(Accepts(schema, R"("wrong type")"));
}

TEST(ServingSchemaContract, OriginalAnswerSchema) {
  const auto path = std::filesystem::path(__FILE__).parent_path() / "schema_contract_cases.json";
  std::ifstream input(path);
  ASSERT_TRUE(input.good()) << path;
  picojson::value fixture;
  ASSERT_TRUE(picojson::parse(fixture, input).empty());
  const auto schema = fixture.get("schema").serialize();
  for (const auto& item : fixture.get("cases").get<picojson::array>()) {
    SCOPED_TRACE(item.get("name").get<std::string>());
    EXPECT_EQ(Accepts(schema, item.get("value").serialize()), item.get("valid").get<bool>());
    picojson::value format;
    ASSERT_TRUE(picojson::parse(format, R"({"type":"structural_tag","format":{
      "type":"tag","begin":"<tool_call>\n<function=submit_final_answer>\n",
      "content":{"type":"json_schema","style":"qwen_xml"},
      "end":"\n</function>\n</tool_call>"}})")
                    .empty());
    format.get("format").get("content").get<picojson::object>()["json_schema"] =
        fixture.get("schema");
    const auto output = "<tool_call>\n<function=submit_final_answer>\n<parameter=answer>" +
                        item.get("value").get("answer").serialize() +
                        "</parameter>\n</function>\n</tool_call>";
    EXPECT_EQ(Accepts(format.serialize(), output, true), item.get("valid").get<bool>());
  }
}

TEST(ServingSchemaContract, EnumKeepsItsSharedTypeAndBounds) {
  const std::string schema = R"({"type":"integer","minimum":2,
    "anyOf":[{"enum":[1,2,"bad"]}]})";
  EXPECT_TRUE(Accepts(schema, "2"));
  EXPECT_FALSE(Accepts(schema, "1"));
  EXPECT_FALSE(Accepts(schema, R"("bad")"));
}

TEST(ServingSchemaContract, ContainsCountsMatchingItemsRatherThanArrayLength) {
  const std::string schema = R"({"type":"array","items":{"enum":[1,2,3]},
    "contains":{"enum":[2]},"minContains":2,"maxContains":2,"maxItems":5})";
  EXPECT_TRUE(Accepts(schema, "[2,2]"));
  EXPECT_TRUE(Accepts(schema, "[3,2,1,2,3]"));
  EXPECT_FALSE(Accepts(schema, "[1,1,2]"));
  EXPECT_FALSE(Accepts(schema, "[2,2,2]"));
  EXPECT_FALSE(Accepts(schema, "[2,2,1,1,1,1]"));
}

TEST(ServingSchemaContract, FiniteValuesRespectConjoinedConstraints) {
  EXPECT_TRUE(
      Accepts(R"({"type":"string","minLength":1,"maxLength":1,"enum":["","é","ab"]})", R"("é")")
  );
  EXPECT_FALSE(
      Accepts(R"({"type":"string","minLength":1,"maxLength":1,"enum":["","é","ab"]})", R"("")")
  );
  EXPECT_FALSE(
      Accepts(R"({"type":"string","minLength":1,"maxLength":1,"enum":["","é","ab"]})", R"("ab")")
  );
  EXPECT_TRUE(Accepts(R"({"type":"number","allOf":[{"type":"integer"},{"minimum":2}]})", "3"));
  EXPECT_FALSE(Accepts(R"({"type":"number","allOf":[{"type":"integer"},{"minimum":2}]})", "3.5"));
  EXPECT_TRUE(Accepts(R"({"type":"integer","anyOf":[{"const":"bad"},{"const":2}]})", "2"));
  EXPECT_TRUE(Accepts(R"({"type":"integer","const":2,"anyOf":[{"minimum":2}]})", "2"));
  EXPECT_THROW(
      Accepts(R"({"type":"integer","const":1,"anyOf":[{"minimum":2}]})", "1"), std::exception
  );
}

TEST(ServingSchemaContract, ContainsCountsIndependentlyAndAllowsZeroMatches) {
  EXPECT_TRUE(Accepts(R"({"type":"array","items":{"enum":[1,2]},"contains":{"const":1.0}})", "[1]")
  );
  EXPECT_FALSE(Accepts(R"({"type":"array","items":{"enum":[1,2]},"contains":{"const":1.0}})", "[2]")
  );
  const std::string zero =
      R"({"type":"array","items":{"enum":[1,2]},"contains":{"const":1},"minContains":0,"maxContains":0})";
  EXPECT_TRUE(Accepts(zero, "[]"));
  EXPECT_TRUE(Accepts(zero, "[2,2]"));
  EXPECT_FALSE(Accepts(zero, "[2,1]"));
  const std::string overlap =
      R"({"type":"array","items":{"enum":[1,2,3]},"allOf":[{"contains":{"enum":[1,2]}},{"contains":{"enum":[2,3]}}]})";
  EXPECT_TRUE(Accepts(overlap, "[2]"));
  EXPECT_TRUE(Accepts(overlap, "[1,3]"));
  EXPECT_FALSE(Accepts(overlap, "[1]"));
  EXPECT_FALSE(Accepts(overlap, "[3]"));
  EXPECT_TRUE(Accepts(
      R"({"type":"integer","allOf":[{"contains":{"const":1}},{"contains":{"const":2}}]})", "5"
  ));
}

TEST(ServingSchemaContract, UnsupportedConstraintsNeverBecomeAnyJson) {
  EXPECT_THROW(
      Accepts(R"({"type":"array","items":{"type":"integer"},"contains":{"const":2}})", "[1]"),
      std::exception
  );
  EXPECT_THROW(
      Accepts(R"({"type":"array","items":{"enum":[1,2]},"contains":{"minimum":2}})", "[1]"),
      std::exception
  );
  EXPECT_THROW(
      Accepts(R"({"type":"integer","allOf":[{"minimum":2},{"not":{"const":3}}]})", "3"),
      std::exception
  );
}

TEST(ServingSchemaContract, ReviewCounterexamples) {
  EXPECT_THROW(
      Accepts(
          R"({"type":"array","enum":[[1]],"items":{"enum":[1,2]},"allOf":[{"contains":{"const":1}}]})",
          "[1,2]"
      ),
      std::exception
  );
  EXPECT_THROW(
      Accepts(
          R"({"type":"array","allOf":[{"prefixItems":[{"type":"integer"}],"items":false},{"items":{"type":"string"}}]})",
          "[1]"
      ),
      std::exception
  );
  const std::string optional =
      R"({"type":"object","additionalProperties":false,"allOf":[{"properties":{"x":{"type":"string"}}},{"properties":{"x":{"type":"integer"}}}]})";
  EXPECT_TRUE(Accepts(optional, "{}"));
  EXPECT_FALSE(Accepts(optional, R"({"x":1})"));
  EXPECT_TRUE(Accepts(
      R"({"anyOf":[{"type":"array","items":{"enum":[1]},"contains":{"const":2}},{"type":"string"}]})",
      R"("ok")"
  ));
  EXPECT_THROW(
      Accepts(
          R"({"type":"integer","oneOf":[{"properties":{"x":{"const":1}},"required":["x"]},{"properties":{"x":{"const":2}},"required":["x"]}]})",
          "3"
      ),
      std::exception
  );
}

TEST(ServingSchemaContract, PreservesExistingUnconjoinedAlternativesAndAnnotations) {
  const std::string ref =
      R"({"$defs":{"A":{"type":"string"}},"anyOf":[{"$ref":"#/$defs/A"},{"type":"null"}]})";
  EXPECT_TRUE(Accepts(ref, R"("a")"));
  EXPECT_TRUE(Accepts(ref, "null"));
  EXPECT_FALSE(Accepts(ref, "7"));
  EXPECT_TRUE(
      Accepts(R"({"anyOf":[{"type":"string","format":"date"},{"type":"null"}]})", R"("2026-09-11")")
  );
  EXPECT_TRUE(Accepts(R"({"anyOf":[{"type":"string","pattern":"^a$"},{"type":"null"}]})", R"("a")")
  );
  const std::string annotation =
      R"({"type":"string","enum":["a"],"deprecated":true,"readOnly":true,"writeOnly":false})";
  EXPECT_TRUE(Accepts(annotation, R"("a")"));
  EXPECT_FALSE(Accepts(annotation, R"("b")"));
}

TEST(ServingSchemaContract, EmptyContainsUsesEmptyArrayWhitespace) {
  const std::string schema =
      R"({"type":"array","items":{"enum":[1,2]},"contains":{"const":1},"minContains":0})";
  EXPECT_TRUE(Accepts(schema, "[]", false, false, 2));
  EXPECT_TRUE(Accepts(schema, "[\n  1\n]", false, false, 2));
  EXPECT_FALSE(Accepts(schema, "[\n]", false, false, 2));
}

TEST(ServingSchemaContract, TokenMaskAndRollbackPreserveContainsState) {
  std::vector<std::string> vocab;
  for (int c = 0; c < 128; ++c) vocab.emplace_back(1, static_cast<char>(c));
  vocab.emplace_back("<eos>");
  xgrammar::GrammarCompiler compiler(
      xgrammar::TokenizerInfo(vocab, xgrammar::VocabType::RAW, 129, std::vector<int32_t>{128}), 1
  );
  auto compiled = compiler.CompileJSONSchema(
      R"({"type":"array","items":{"enum":[1,2]},"allOf":[{"contains":{"const":1}},{"contains":{"const":2}}]})"
  );
  xgrammar::GrammarMatcher matcher(compiled, std::nullopt, false, 8);
  int32_t data[5] = {};
  int64_t shape[2] = {1, 5};
  DLTensor tensor{data, {kDLCPU, 0}, 2, {kDLInt, 32, 1}, shape, nullptr, 0};
  auto allowed = [&](int token) {
    matcher.FillNextTokenBitmask(&tensor);
    return (static_cast<uint32_t>(data[token / 32]) & (uint32_t{1} << (token % 32))) != 0;
  };
  ASSERT_TRUE(matcher.AcceptToken('['));
  ASSERT_TRUE(matcher.AcceptToken('1'));
  EXPECT_FALSE(allowed(']'));
  EXPECT_TRUE(allowed(','));
  ASSERT_TRUE(matcher.AcceptToken(','));
  ASSERT_TRUE(matcher.AcceptToken('2'));
  EXPECT_TRUE(allowed(']'));
  EXPECT_FALSE(allowed(128));
  matcher.Rollback(2);
  EXPECT_FALSE(allowed(']'));
  EXPECT_TRUE(allowed(','));
  ASSERT_TRUE(matcher.AcceptToken(','));
  ASSERT_TRUE(matcher.AcceptToken('2'));
  ASSERT_TRUE(matcher.AcceptToken(']'));
  EXPECT_TRUE(allowed(128));
}

TEST(ServingSchemaContract, ImpossibleOptionalFieldCanBeOmitted) {
  const std::string number =
      R"({"type":"object","properties":{"x":{"type":"integer","minimum":2}},"additionalProperties":false,"anyOf":[{"properties":{"x":{"const":1}}}]})";
  EXPECT_TRUE(Accepts(number, "{}"));
  EXPECT_FALSE(Accepts(number, R"({"x":1})"));
  const std::string text =
      R"({"type":"object","properties":{"x":{"type":"string","minLength":2}},"additionalProperties":false,"anyOf":[{"properties":{"x":{"maxLength":1}}}]})";
  EXPECT_TRUE(Accepts(text, "{}"));
  EXPECT_FALSE(Accepts(text, R"({"x":"a"})"));
}
TEST(ServingSchemaContract, UntypedConjunctionCannotLoseBounds) {
  EXPECT_THROW(Accepts(R"({"allOf":[{"minimum":2},{"maximum":4}]})", "1"), std::exception);
}

TEST(ServingSchemaContract, FinalReviewContainerAndTypeBoundaries) {
  EXPECT_THROW(
      Accepts(
          R"({"type":"object","properties":{"x":false},"patternProperties":{"^x$":{"type":"integer"}},"additionalProperties":false})",
          R"({"x":1})"
      ),
      std::exception
  );
  const std::string nullable =
      R"({"type":["object","null"],"allOf":[{"properties":{"x":{"type":"integer"}},"required":["x"]},{"properties":{"x":{"type":"string"}}}]})";
  EXPECT_TRUE(Accepts(nullable, "null"));
  EXPECT_FALSE(Accepts(nullable, R"({"x":1})"));
  const std::string array =
      R"({"type":"array","items":{"type":"integer","minimum":2},"allOf":[{"items":{"maximum":1}}]})";
  EXPECT_TRUE(Accepts(array, "[]"));
  EXPECT_FALSE(Accepts(array, "[1]"));
  const std::string object =
      R"({"type":"object","additionalProperties":{"type":"integer","minimum":2},"allOf":[{"additionalProperties":{"maximum":1}}]})";
  EXPECT_TRUE(Accepts(object, "{}"));
  EXPECT_FALSE(Accepts(object, R"({"x":1})"));
  EXPECT_TRUE(
      Accepts(R"({"type":["array","null"],"items":{"enum":[1]},"contains":{"const":2}})", "null")
  );
  EXPECT_TRUE(Accepts(
      R"({"anyOf":[{"type":"array","items":{"enum":[1]},"contains":{"const":1},"minContains":2,"maxContains":1},{"type":"string"}]})",
      R"("ok")"
  ));
}

TEST(ServingSchemaContract, MetadataAndExactIntegerIdentity) {
  EXPECT_TRUE(Accepts(
      R"({"$id":"https://example.com/color","$anchor":"Color","type":"string","enum":["red","blue"]})",
      R"("red")"
  ));
  EXPECT_TRUE(Accepts(
      R"({"$id":"https://example.com/color","anyOf":[{"const":"red"},{"const":"blue"}]})",
      R"("blue")"
  ));
  EXPECT_TRUE(Accepts(
      R"({"oneOf":[{"const":9007199254740992},{"const":9007199254740993}]})", "9007199254740993"
  ));
  const std::string array =
      R"({"type":"array","items":{"enum":[9007199254740992,9007199254740993]},"contains":{"const":9007199254740993}})";
  EXPECT_TRUE(Accepts(array, "[9007199254740993]"));
  EXPECT_FALSE(Accepts(array, "[9007199254740992]"));
  EXPECT_THROW(
      Accepts(R"({"enum":[9007199254740992],"minimum":9007199254740993})", "9007199254740992"),
      std::exception
  );
}

TEST(ServingSchemaContract, ScopedReferencesRemainUnsupported) {
  EXPECT_THROW(
      Accepts(
          R"({"type":"object","$defs":{"X":{"const":"outer"}},"properties":{"child":{"$id":"https://example.com/child","$defs":{"X":{"const":"inner"}},"anyOf":[{"$ref":"#/$defs/X"}]}},"required":["child"],"additionalProperties":false})",
          R"({"child":"outer"})"
      ),
      std::exception
  );
}

TEST(ServingSchemaContract, DescendantScopedReferencesRemainUnsupported) {
  for (const std::string op : {"anyOf", "allOf"}) {
    const std::string schema =
        R"({"type":"object","$defs":{"X":{"const":"outer"}},"properties":{"child":{"$id":"https://example.com/child","$defs":{"X":{"const":"inner"}},")" +
        op +
        R"(":[{"type":"object","properties":{"x":{"$ref":"#/$defs/X"}},"required":["x"],"additionalProperties":false}]}},"required":["child"],"additionalProperties":false})";
    EXPECT_THROW(Accepts(schema, R"({"child":{"x":"outer"}})"), std::exception);
  }
}

TEST(ServingSchemaContract, UnusedResourceIdentifierDoesNotChangeReferenceScope) {
  EXPECT_TRUE(Accepts(
      R"({"$defs":{"Unused":{"$id":"https://example.com/unused"},"X":{"type":"integer"}},"anyOf":[{"$ref":"#/$defs/X"}]})",
      "3"
  ));
}
}  // namespace
