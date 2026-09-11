/* Copyright (c) 2026 by Contributors. */
#ifndef XGRAMMAR_SCHEMA_CONJUNCTION_H_
#define XGRAMMAR_SCHEMA_CONJUNCTION_H_

#include <picojson.h>

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <string>
#include <vector>

#include "support/encoding.h"

namespace xgrammar {
namespace schema_conjunction {
using Value = picojson::value;
using Object = picojson::object;
using Array = picojson::array;

inline bool IsFalse(const Value& v) { return v.is<bool>() && !v.get<bool>(); }
inline bool IsTrue(const Value& v) { return v.is<bool>() && v.get<bool>(); }
inline bool IsAnnotation(const std::string& k) {
  return k == "title" || k == "description" || k == "default" || k == "examples" ||
         k == "$comment" || k == "$schema" || k == "deprecated" || k == "readOnly" ||
         k == "writeOnly";
}
// Identifiers do not restrict finite values, but are not identity conjuncts:
// a resource ID can change the meaning of a reference in another conjunct.
inline bool IsIdentifier(const std::string& k) { return k == "$id" || k == "$anchor"; }
// Walk schema-valued keywords only; const/enum/default/examples are instance data.
inline bool HasSchemaKeyword(const Value& v, const std::string& keyword) {
  if (!v.is<Object>()) return false;
  const auto& o = v.get<Object>();
  if (o.count(keyword)) return true;
  for (const auto& key :
       {"items",
        "additionalItems",
        "additionalProperties",
        "unevaluatedItems",
        "unevaluatedProperties",
        "contains",
        "propertyNames",
        "not",
        "if",
        "then",
        "else"})
    if (o.count(key) && HasSchemaKeyword(o.at(key), keyword)) return true;
  for (const auto& key : {"anyOf", "oneOf", "allOf", "prefixItems"})
    if (o.count(key) && o.at(key).is<Array>())
      for (const auto& child : o.at(key).get<Array>())
        if (HasSchemaKeyword(child, keyword)) return true;
  for (const auto& key :
       {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"})
    if (o.count(key) && o.at(key).is<Object>())
      for (const auto& entry : o.at(key).get<Object>())
        if (HasSchemaKeyword(entry.second, keyword)) return true;
  return false;
}
inline bool IsNumber(const Value& v) { return v.is<int64_t>() || v.is<double>(); }
inline long double Number(const Value& v) {
  if (!IsNumber(v)) throw std::runtime_error("expected a numeric schema bound");
  return v.is<int64_t>() ? static_cast<long double>(v.get<int64_t>()) : v.get<double>();
}
// Preserve integer identity even where long double has only 53 mantissa bits.
inline int CompareNumbers(const Value& a, const Value& b) {
  if (!IsNumber(a) || !IsNumber(b)) throw std::runtime_error("expected numbers");
  if (a.is<int64_t>() && b.is<int64_t>()) {
    const auto x = a.get<int64_t>(), y = b.get<int64_t>();
    return (x > y) - (x < y);
  }
  if (a.is<int64_t>()) {
    const auto x = a.get<int64_t>();
    const double y = b.get<double>();
    // Check the conversion range before truncating the finite JSON number.
    if (y >= 9223372036854775808.0) return -1;
    if (y < -9223372036854775808.0) return 1;
    const auto truncated = static_cast<int64_t>(y);
    if (x != truncated) return (x > truncated) - (x < truncated);
    const double integral = static_cast<double>(truncated);
    return (integral > y) - (integral < y);
  }
  if (b.is<int64_t>()) return -CompareNumbers(b, a);
  const auto x = a.get<double>(), y = b.get<double>();
  return (x > y) - (x < y);
}
// JSON Schema compares numbers by value, not by their JSON spelling (1 == 1.0).
inline bool Equal(const Value& a, const Value& b) {
  if (IsNumber(a) && IsNumber(b)) return CompareNumbers(a, b) == 0;
  if (a.is<Array>() && b.is<Array>()) {
    const auto& x = a.get<Array>();
    const auto& y = b.get<Array>();
    return x.size() == y.size() && std::equal(x.begin(), x.end(), y.begin(), Equal);
  }
  if (a.is<Object>() && b.is<Object>()) {
    const auto& x = a.get<Object>();
    const auto& y = b.get<Object>();
    if (x.size() != y.size()) return false;
    for (const auto& [k, v] : x)
      if (!y.count(k) || !Equal(v, y.at(k))) return false;
    return true;
  }
  return a == b;
}
inline bool HasType(const Value& v, const Value& type) {
  if (type.is<Array>()) {
    for (const auto& t : type.get<Array>())
      if (HasType(v, t)) return true;
    return false;
  }
  if (!type.is<std::string>()) throw std::runtime_error("type must be a string or array");
  const auto& t = type.get<std::string>();
  if (t == "number") return IsNumber(v);
  if (t == "integer") return IsNumber(v) && std::floor(Number(v)) == Number(v);
  if (t == "string") return v.is<std::string>();
  if (t == "boolean") return v.is<bool>();
  if (t == "null") return v.is<picojson::null>();
  if (t == "array") return v.is<Array>();
  if (t == "object") return v.is<Object>();
  throw std::runtime_error("unknown JSON Schema type: " + t);
}
inline Value IntersectTypes(const Value& a, const Value& b) {
  const Array left = a.is<Array>() ? a.get<Array>() : Array{a};
  const Array right = b.is<Array>() ? b.get<Array>() : Array{b};
  Array intersection;
  for (const auto& x : left)
    for (const auto& y : right) {
      Value t;
      if (x == y)
        t = x;
      else if ((x == Value("number") && y == Value("integer")) ||
               (x == Value("integer") && y == Value("number")))
        t = Value("integer");
      else
        continue;
      if (std::find(intersection.begin(), intersection.end(), t) == intersection.end())
        intersection.push_back(t);
    }
  if (intersection.empty()) return Value(false);
  return intersection.size() == 1 ? intersection.front() : Value(intersection);
}

// Finite enum/const languages can be filtered exactly before grammar generation.
// Do not let the parser's enum precedence discard an intersected type or bound.
// Other assertion kinds are rejected explicitly, not approximated.
inline Value FilterFinite(const Object& schema) {
  Array candidates;
  if (schema.count("enum"))
    candidates = schema.at("enum").get<Array>();
  else if (schema.count("const"))
    candidates.push_back(schema.at("const"));
  else
    throw std::runtime_error("finite schema requires enum or const");
  for (const auto& [k, ignored] : schema) {
    if (IsAnnotation(k) || IsIdentifier(k) || k == "$defs" || k == "definitions" || k == "enum" ||
        k == "const" || k == "type" || k == "minimum" || k == "maximum" ||
        k == "exclusiveMinimum" || k == "exclusiveMaximum" || k == "minLength" || k == "maxLength")
      continue;
    throw std::runtime_error("unsupported finite schema conjunction: " + k);
  }
  Array kept;
  for (const auto& value : candidates) {
    if (schema.count("const") && !Equal(value, schema.at("const"))) continue;
    if (schema.count("type") && !HasType(value, schema.at("type"))) continue;
    if (IsNumber(value)) {
      if (schema.count("minimum") && CompareNumbers(value, schema.at("minimum")) < 0) continue;
      if (schema.count("maximum") && CompareNumbers(value, schema.at("maximum")) > 0) continue;
      if (schema.count("exclusiveMinimum") &&
          CompareNumbers(value, schema.at("exclusiveMinimum")) <= 0)
        continue;
      if (schema.count("exclusiveMaximum") &&
          CompareNumbers(value, schema.at("exclusiveMaximum")) >= 0)
        continue;
    }
    if (value.is<std::string>()) {
      const auto& text = value.get<std::string>();
      size_t offset = 0, length = 0;
      while (offset < text.size()) {
        auto [codepoint, bytes] = ParseNextUTF8(text.c_str() + offset);
        if (codepoint < 0 || bytes <= 0) throw std::runtime_error("invalid UTF-8 enum");
        offset += bytes;
        ++length;
      }
      if (schema.count("minLength") && length < Number(schema.at("minLength"))) continue;
      if (schema.count("maxLength") && length > Number(schema.at("maxLength"))) continue;
    }
    kept.push_back(value);
  }
  if (kept.empty()) return Value(false);
  return schema.count("const") ? Value(Object{{"const", schema.at("const")}})
                               : Value(Object{{"enum", Value(kept)}});
}
inline bool ContainsOnly(const Value& v) {
  if (!v.is<Object>() || !v.get<Object>().count("contains")) return false;
  for (const auto& [k, ignored] : v.get<Object>()) {
    if (k != "contains" && k != "minContains" && k != "maxContains" && !IsAnnotation(k))
      return false;
  }
  return true;
}
inline bool ContainsConjunction(const Object& o) {
  if (!o.count("allOf") || !o.at("allOf").is<Array>()) return false;
  const auto& a = o.at("allOf").get<Array>();
  return !a.empty() && std::all_of(a.begin(), a.end(), ContainsOnly);
}

// Union implements oneOf only when alternatives are provably disjoint. This
// covers typed discriminated unions without pretending arbitrary overlaps work.
inline bool Disjoint(const Value& left, const Value& right) {
  if (IsFalse(left) || IsFalse(right)) return true;
  if (!left.is<Object>() || !right.is<Object>()) return false;
  const auto& a = left.get<Object>();
  const auto& b = right.get<Object>();
  if (a.count("type") && b.count("type") && IsFalse(IntersectTypes(a.at("type"), b.at("type"))))
    return true;
  if ((a.count("const") || a.count("enum")) && (b.count("const") || b.count("enum"))) {
    const Array x = a.count("const") ? Array{a.at("const")} : a.at("enum").get<Array>();
    const Array y = b.count("const") ? Array{b.at("const")} : b.at("enum").get<Array>();
    for (const auto& i : x)
      for (const auto& j : y)
        if (Equal(i, j)) return false;
    return true;
  }
  if (a.count("type") && b.count("type") && a.at("type") == Value("object") &&
      b.at("type") == Value("object") && a.count("properties") && b.count("properties") &&
      a.count("required") && b.count("required")) {
    const auto& pa = a.at("properties").get<Object>();
    const auto& pb = b.at("properties").get<Object>();
    const auto& required_b = b.at("required").get<Array>();
    for (const auto& key : a.at("required").get<Array>()) {
      const auto& name = key.get<std::string>();
      if (pa.count(name) && pb.count(name) &&
          std::find(required_b.begin(), required_b.end(), key) != required_b.end() &&
          Disjoint(pa.at(name), pb.at(name)))
        return true;
    }
  }
  return false;
}

inline bool IsIdentity(const Value& v) {
  if (IsTrue(v)) return true;
  if (!v.is<Object>()) return false;
  for (const auto& [key, ignored] : v.get<Object>())
    if (!IsAnnotation(key) && key != "$defs" && key != "definitions") return false;
  return true;
}

inline void CheckConjunct(const Value& v) {
  if (!v.is<Object>()) return;
  for (const auto& [key, ignored] : v.get<Object>()) {
    if (key == "prefixItems") throw std::runtime_error("tuple schema conjunctions are unsupported");
    if (IsAnnotation(key) || IsIdentifier(key) || key == "$defs" || key == "definitions" ||
        key == "type" || key == "properties" || key == "required" ||
        key == "additionalProperties" || key == "items" || key == "prefixItems" ||
        key == "contains" || key == "minContains" || key == "maxContains" || key == "anyOf" ||
        key == "oneOf" || key == "allOf" || key == "enum" || key == "const" || key == "minimum" ||
        key == "maximum" || key == "exclusiveMinimum" || key == "exclusiveMaximum" ||
        key == "minItems" || key == "maxItems" || key == "minLength" || key == "maxLength" ||
        key == "minProperties" || key == "maxProperties")
      continue;
    throw std::runtime_error("unsupported schema conjunction keyword: " + key);
  }
}

inline Value Conjoin(const Value& left, const Value& right);

inline Value Collapse(const Value& v) {
  if (!v.is<Object>()) return v;
  auto o = v.get<Object>();
  if (!o.count("allOf") || ContainsConjunction(o)) return v;
  if (!o.at("allOf").is<Array>()) throw std::runtime_error("allOf must be an array");
  auto parts = o.at("allOf").get<Array>();
  o.erase("allOf");
  Value result(o);
  for (const auto& part : parts) result = Conjoin(result, Collapse(part));
  return result;
}

inline Value Conjoin(const Value& left_input, const Value& right_input) {
  // The upstream resolver has one root document, not a resource-scope stack.
  // Keep scoped references unsupported even when nested inside an alternative.
  if ((HasSchemaKeyword(left_input, "$id") || HasSchemaKeyword(right_input, "$id") ||
       HasSchemaKeyword(left_input, "$anchor") || HasSchemaKeyword(right_input, "$anchor")) &&
      (HasSchemaKeyword(left_input, "$ref") || HasSchemaKeyword(right_input, "$ref")))
    throw std::runtime_error("resource-scoped reference conjunctions are unsupported");
  Value left = Collapse(left_input), right = Collapse(right_input);
  if (IsFalse(left) || IsFalse(right)) return Value(false);
  if (IsIdentity(left)) return right;
  if (IsIdentity(right)) return left;
  CheckConjunct(left);
  CheckConjunct(right);
  if (!left.is<Object>() || !right.is<Object>())
    throw std::runtime_error("schema conjunction requires objects or booleans");
  Object a = left.get<Object>(), b = right.get<Object>();
  if (a.empty()) return right;
  if (b.empty()) return left;

  // Distribute a conjunction into alternatives before parsing them. Each alternative
  // retains every shared constraint; merely parsing an option loses the siblings.
  for (const std::string key : {"anyOf", "oneOf"}) {
    if (a.count(key)) {
      if (!a.at(key).is<Array>()) throw std::runtime_error(key + " must be an array");
      Array choices = a.at(key).get<Array>(), result;
      a.erase(key);
      for (const auto& choice : choices) {
        auto merged = Conjoin(Conjoin(Value(a), choice), right);
        if (!IsFalse(merged)) result.push_back(std::move(merged));
      }
      return result.empty() ? Value(false) : Value(Object{{key, Value(result)}});
    }
    if (b.count(key)) {
      if (!b.at(key).is<Array>()) throw std::runtime_error(key + " must be an array");
      Array choices = b.at(key).get<Array>(), result;
      b.erase(key);
      for (const auto& choice : choices) {
        auto merged = Conjoin(left, Conjoin(Value(b), choice));
        if (!IsFalse(merged)) result.push_back(std::move(merged));
      }
      return result.empty() ? Value(false) : Value(Object{{key, Value(result)}});
    }
  }
  if (a.count("$ref") || b.count("$ref") || a.count("patternProperties") ||
      b.count("patternProperties") || a.count("unevaluatedProperties") ||
      b.count("unevaluatedProperties"))
    throw std::runtime_error("unsupported reference or pattern-property conjunction");

  // additionalProperties is local to its own properties map, not the merged map.
  // Retain that distinction when combining closed object schemas.
  Object properties;
  bool has_properties = a.count("properties") || b.count("properties");
  if (has_properties) {
    auto pa = a.count("properties") ? a.at("properties").get<Object>() : Object{};
    auto pb = b.count("properties") ? b.at("properties").get<Object>() : Object{};
    auto aa = a.count("additionalProperties") ? a.at("additionalProperties") : Value(true);
    auto ab = b.count("additionalProperties") ? b.at("additionalProperties") : Value(true);
    for (const auto& name : pa.ordered_keys())
      properties[name] = Conjoin(pa.at(name), pb.count(name) ? pb.at(name) : ab);
    for (const auto& name : pb.ordered_keys())
      if (!pa.count(name)) properties[name] = Conjoin(aa, pb.at(name));
  }

  // Multiple contains predicates must be counted independently. Keep them as an
  // array conjunction for ParseArray instead of replacing either predicate.
  Array contains;
  for (Object* o : {&a, &b}) {
    if (o->count("contains")) {
      Object clause;
      for (const auto& key : {"contains", "minContains", "maxContains"}) {
        auto it = o->find(key);
        if (it != o->end()) {
          clause[key] = it->second;
          o->erase(it);
        }
      }
      contains.emplace_back(clause);
    }
    if (ContainsConjunction(*o)) {
      const auto& clauses = o->at("allOf").get<Array>();
      contains.insert(contains.end(), clauses.begin(), clauses.end());
      o->erase("allOf");
    }
  }
  Object result = a;
  for (const auto& [key, value] : b) {
    if (key == "properties" || IsAnnotation(key)) continue;
    if (!result.count(key)) {
      result[key] = value;
      continue;
    }
    auto& prior = result[key];
    if (prior == value) continue;
    if (key == "required") {
      auto union_items = prior.get<Array>();
      for (const auto& item : value.get<Array>())
        if (std::find(union_items.begin(), union_items.end(), item) == union_items.end())
          union_items.push_back(item);
      prior = Value(union_items);
    } else if (key == "enum") {
      Array intersection;
      for (const auto& item : prior.get<Array>())
        if (std::any_of(
                value.get<Array>().begin(),
                value.get<Array>().end(),
                [&](const Value& other) { return Equal(item, other); }
            ))
          intersection.push_back(item);
      if (intersection.empty()) return Value(false);
      prior = Value(intersection);
    } else if (key == "items" || key == "additionalProperties") {
      prior = Conjoin(prior, value);
    } else if (key == "minimum" || key == "exclusiveMinimum" || key == "minItems" ||
               key == "minLength" || key == "minProperties") {
      if (CompareNumbers(value, prior) > 0) prior = value;
    } else if (key == "maximum" || key == "exclusiveMaximum" || key == "maxItems" ||
               key == "maxLength" || key == "maxProperties") {
      if (CompareNumbers(value, prior) < 0) prior = value;
    } else if (key == "const") {
      if (!Equal(prior, value)) return Value(false);
    } else if (key == "type") {
      prior = IntersectTypes(prior, value);
      if (IsFalse(prior)) return Value(false);
    } else {
      throw std::runtime_error("unsupported schema conjunction for " + key);
    }
  }
  if (has_properties) {
    const auto required = result.count("required") ? result.at("required").get<Array>() : Array{};
    std::vector<std::string> prohibited;
    for (const auto& name : properties.ordered_keys()) {
      if (!IsFalse(properties.at(name))) continue;
      if (std::find(required.begin(), required.end(), Value(name)) != required.end())
        continue;  // ParseObject rejects only the object branch of a type union.
      if (!result.count("additionalProperties") || !IsFalse(result.at("additionalProperties")))
        throw std::runtime_error(
            "prohibited optional properties in open intersections are unsupported"
        );
      prohibited.push_back(name);
    }
    // A closed schema still prohibits a removed impossible optional property.
    for (const auto& name : prohibited) properties.erase(name);
    result["properties"] = Value(properties);
  }
  if (!contains.empty()) result["allOf"] = Value(contains);
  return Value(result);
}
}  // namespace schema_conjunction
}  // namespace xgrammar
#endif
