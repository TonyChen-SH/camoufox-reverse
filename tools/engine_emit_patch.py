"""Generate SpiderMonkey property-observation changes for the pinned source."""

PATHS = (
    "js/src/frontend/BytecodeEmitter.h",
    "js/src/frontend/BytecodeEmitter.cpp",
    "js/src/frontend/PropOpEmitter.h",
    "js/src/frontend/PropOpEmitter.cpp",
    "js/src/frontend/ElemOpEmitter.h",
    "js/src/frontend/ElemOpEmitter.cpp",
    "js/src/vm/SelfHosting.cpp",
    "js/src/builtin/Reflect.cpp",
    "js/src/builtin/Reflect.js",
    "js/src/frontend/StencilXdr.cpp",
)

OBSERVE_DECLARATION = '''  [[nodiscard]] bool emitCamoufoxObserve(uint8_t operation, unsigned receiverDepth,
                           mozilla::Maybe<GCThingIndex> keyAtom,
                           unsigned keyDepth,
                           mozilla::Maybe<unsigned> valueDepth,
                           const mozilla::Maybe<uint32_t>& sourceCoordOffset = mozilla::Nothing());

'''

OBSERVE_HELPER = '''bool BytecodeEmitter::emitCamoufoxObserve(
    uint8_t operation, unsigned receiverDepth,
    mozilla::Maybe<GCThingIndex> keyAtom, unsigned keyDepth,
    mozilla::Maybe<unsigned> valueDepth,
    const mozilla::Maybe<uint32_t>& sourceCoordOffset) {
  if (emitterMode == SelfHosting || !js::CamoufoxMonitorEnabled()) {
    return true;
  }

  constexpr char intrinsic[] = "CamoufoxObserve";
  auto name = parserAtoms().internAscii(fc, intrinsic, sizeof(intrinsic) - 1);
  if (!name || !emitAtomOp(JSOp::GetIntrinsic, name) ||
      !emit1(JSOp::Undefined) || !emitNumberOp(operation)) {
    return false;
  }

  // The original operands remain on the stack throughout the observer call.
  if (!emitDupAt(receiverDepth + 3)) {
    return false;
  }
  if (keyAtom) {
    if (!emitStringOp(JSOp::String, *keyAtom)) {
      return false;
    }
  } else if (!emitDupAt(keyDepth + 4)) {
    return false;
  }
  if (valueDepth) {
    if (!emitDupAt(*valueDepth + 5)) {
      return false;
    }
  } else if (!emit1(JSOp::Undefined)) {
    return false;
  }
  return emitCall(JSOp::Call, 4, sourceCoordOffset) && emit1(JSOp::Pop);
}

'''

ATOM_HOOK = '''  switch (op) {
    case JSOp::GetProp:
    case JSOp::GetPropSuper:
      if (!emitCamoufoxObserve(0, 0, mozilla::Some(atomIndex), 0,
                              mozilla::Nothing(), sourceCoordOffset)) {
        return false;
      }
      break;
    case JSOp::SetProp:
    case JSOp::StrictSetProp:
    case JSOp::SetPropSuper:
    case JSOp::StrictSetPropSuper:
      if (!emitCamoufoxObserve(1, 1, mozilla::Some(atomIndex), 0,
                              mozilla::Some(0u), sourceCoordOffset)) {
        return false;
      }
      break;
    default:
      break;
  }
'''

ELEMENT_HOOK = '''  switch (op) {
    case JSOp::GetElem:
      if (!emitCamoufoxObserve(0, 1, mozilla::Nothing(), 0,
                              mozilla::Nothing(), sourceCoordOffset)) {
        return false;
      }
      break;
    case JSOp::GetElemSuper:
      if (!emitCamoufoxObserve(0, 0, mozilla::Nothing(), 1,
                              mozilla::Nothing(), sourceCoordOffset)) {
        return false;
      }
      break;
    case JSOp::SetElem:
    case JSOp::StrictSetElem:
      if (!emitCamoufoxObserve(1, 2, mozilla::Nothing(), 1,
                              mozilla::Some(0u), sourceCoordOffset)) {
        return false;
      }
      break;
    case JSOp::SetElemSuper:
    case JSOp::StrictSetElemSuper:
      if (!emitCamoufoxObserve(1, 1, mozilla::Nothing(), 2,
                              mozilla::Some(0u), sourceCoordOffset)) {
        return false;
      }
      break;
    case JSOp::In:
      if (!emitCamoufoxObserve(2, 0, mozilla::Nothing(), 1,
                              mozilla::Nothing(), sourceCoordOffset)) {
        return false;
      }
      break;
    default:
      break;
  }
'''

INTRINSIC = '''static bool intrinsic_CamoufoxObserve(JSContext* cx, unsigned argc,
                                      Value* vp) {
  CallArgs args = CallArgsFromVp(argc, vp);
  MOZ_ASSERT(args.length() == 4);
  MOZ_ASSERT(args[0].isInt32());
  CamoufoxMonitorProperty(cx, uint32_t(args[0].toInt32()), args[1], args[2],
                         args[3]);
  args.rval().setUndefined();
  return true;
}

'''

SELF_HOSTING_GATE = '''    if (!js::CamoufoxMonitorEnabled()) {
      constexpr char intrinsic[] = "CamoufoxObserve";
      auto observer = parserAtoms().internAscii(fc, intrinsic,
                                                sizeof(intrinsic) - 1);
      if (!observer) {
        return false;
      }
      if (calleeName == observer) {
        return emit1(JSOp::Undefined);
      }
    }
'''


SELF_HOSTING_STATEMENT_GATE = '''  if (!wantval && emitterMode == SelfHosting &&
      !js::CamoufoxMonitorEnabled() && expr->isKind(ParseNodeKind::CallExpr)) {
    CallNode* call = &expr->as<CallNode>();
    ParseNode* callee = call->callee();
    if (call->callOp() == JSOp::Call && callee->isKind(ParseNodeKind::Name)) {
      constexpr char intrinsic[] = "CamoufoxObserve";
      auto observer = parserAtoms().internAscii(fc, intrinsic,
                                                sizeof(intrinsic) - 1);
      if (!observer) {
        return false;
      }
      if (callee->as<NameNode>().name() == observer) {
        return true;
      }
    }
  }
'''


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Engine source anchor is missing or ambiguous; port the patch first")
    return text.replace(old, new, 1)


def replace_in_function(text, name, old, new):
    signature = f"bool BytecodeEmitter::{name}("
    if text.count(signature) != 1:
        raise ValueError("Engine function anchor is missing or ambiguous; port the patch first")
    begin = text.index(signature)
    end = text.index("\n}\n", begin) + 3
    return text[:begin] + replace_once(text[begin:end], old, new) + text[end:]


def operation_coordinates(text):
    for name, indent, emitter in (
        ("emitPropIncDec", "  ", "poe"),
        ("emitDestructuringLHSRef", "      ", "poe"),
        ("emitAssignmentOrInit", "      ", "poe->"),
        ("emitShortCircuitAssignment", "      ", "poe->"),
        ("emitCalleeAndThis", "      ", "poe"),
        ("emitOptionalDotExpression", "  ", "poe"),
        ("emitTree", "      ", "poe"),
    ):
        access = emitter if emitter.endswith("->") else emitter + "."
        anchor = indent + f"if (!{access}prepareForObj()) {{\n"
        text = replace_in_function(text, name, anchor,
                                   indent + f"{access}setCamoufoxSourceCoord(Some(prop->pn_pos.begin));\n" + anchor)
    for name in ("emitElemObjAndKey", "emitOptionalElemExpression"):
        anchor = "  if (!eoe.prepareForObj()) {\n"
        text = replace_in_function(text, name, anchor,
                                   "  eoe.setCamoufoxSourceCoord(Some(elem->pn_pos.begin));\n" + anchor)
    anchor = "    if (!emitAtomOp(JSOp::GetProp, pndot->key().atom())) {\n"
    text = replace_in_function(text, "emitPropLHS", anchor,
                               "    if (!emitAtomOp(JSOp::GetProp, pndot->key().atom(),\n"
                               "                    Some(pndot->pn_pos.begin))) {\n")
    anchor = "    if (!emit1(op)) {\n"
    text = replace_in_function(text, "emitLeftAssociative", anchor,
                               "    if (!emit1(op, op == JSOp::In ? Some(node->pn_pos.begin) : Nothing())) {\n")
    for name, owner in (("emitTree", "pn"), ("emitOptionalTree", "pn"),
                        ("emitCalleeAndThis", "callee"), ("emitOptionalCalleeAndThis", "callee")):
        text = replace_in_function(text, name, "if (!emitArgumentsLength()) {",
                                   f"if (!emitArgumentsLength(Some({owner}->pn_pos.begin))) {{")
    text = replace_in_function(text, "emitArgumentsLength", "  if (!poe.prepareForObj()) {\n",
                               "  poe.setCamoufoxSourceCoord(sourceCoordOffset);\n"
                               "  if (!poe.prepareForObj()) {\n")
    return text


def transform(path, data):
    text = data.decode("utf-8")
    if path == "js/src/frontend/BytecodeEmitter.h":
        anchor = "  [[nodiscard]] bool emitCheckIsCallable();\n"
        text = replace_once(text, anchor, OBSERVE_DECLARATION + anchor)
        for name, arguments in (("emit1", "JSOp op"),
                                ("emitAtomOp", "JSOp op, TaggedParserAtomIndex atom"),
                                ("emitAtomOp", "JSOp op, GCThingIndex atomIndex"),
                                ("emitElemOpBase", "JSOp op"),
                                ("emitArgumentsLength", "")):
            anchor = f"  [[nodiscard]] bool {name}({arguments});\n"
            prefix = arguments + ",\n      " if arguments else "\n      "
            text = replace_once(text, anchor,
                                f"  [[nodiscard]] bool {name}({prefix}"
                                "const mozilla::Maybe<uint32_t>& sourceCoordOffset = mozilla::Nothing());\n")
    elif path == "js/src/frontend/BytecodeEmitter.cpp":
        anchor = '#include "vm/BytecodeUtil.h"'
        text = replace_once(text, anchor, '#include "vm/CamoufoxMonitor.h"\n' + anchor)
        anchor = "bool BytecodeEmitter::emitCheckIsCallable() {\n"
        text = replace_once(text, anchor, OBSERVE_HELPER + anchor)
        anchor = "bool BytecodeEmitter::emit1(JSOp op) {\n"
        text = replace_once(text, anchor, anchor + ELEMENT_HOOK)
        anchor = "bool BytecodeEmitter::emitAtomOp(JSOp op, GCThingIndex atomIndex) {\n"
        text = replace_once(text, anchor, anchor + ATOM_HOOK)
        anchor = "    auto calleeName = calleeNode->as<NameNode>().name();\n"
        text = replace_once(text, anchor, anchor + SELF_HOSTING_GATE)
        anchor = "  ParseNode* expr = exprStmt->kid();\n"
        text = replace_in_function(text, "emitExpressionStatement", anchor,
                                   anchor + SELF_HOSTING_STATEMENT_GATE)
        for name, arguments in (("emit1", "JSOp op"),
                                ("emitAtomOp", "JSOp op, TaggedParserAtomIndex atom"),
                                ("emitAtomOp", "JSOp op, GCThingIndex atomIndex"),
                                ("emitElemOpBase", "JSOp op"),
                                ("emitArgumentsLength", "")):
            anchor = f"bool BytecodeEmitter::{name}({arguments}) {{\n"
            prefix = arguments + ",\n    " if arguments else "\n    "
            text = replace_once(text, anchor,
                                f"bool BytecodeEmitter::{name}({prefix}"
                                "const Maybe<uint32_t>& sourceCoordOffset) {\n")
        text = replace_once(text, "  return emitAtomOp(op, index);\n",
                            "  return emitAtomOp(op, index, sourceCoordOffset);\n")
        text = replace_in_function(text, "emitElemOpBase", "  if (!emit1(op)) {\n",
                                   "  if (!emit1(op, sourceCoordOffset)) {\n")
        text = operation_coordinates(text)
    elif path in ("js/src/frontend/PropOpEmitter.h", "js/src/frontend/ElemOpEmitter.h"):
        anchor = '#include "mozilla/Attributes.h"\n'
        text = replace_once(text, anchor, anchor + '#include "mozilla/Maybe.h"\n')
        anchor = "#include <stddef.h>\n"
        text = replace_once(text, anchor, anchor + "#include <stdint.h>\n")
        anchor = "  BytecodeEmitter* bce_;\n"
        text = replace_once(text, anchor, anchor + "  mozilla::Maybe<uint32_t> camoufoxSourceCoord_;\n")
        anchor = "  [[nodiscard]] bool prepareForObj();\n"
        text = replace_once(text, anchor, '''  void setCamoufoxSourceCoord(mozilla::Maybe<uint32_t> offset) {
    camoufoxSourceCoord_ = offset;
  }

''' + anchor)
    elif path in ("js/src/frontend/PropOpEmitter.cpp", "js/src/frontend/ElemOpEmitter.cpp"):
        if "PropOpEmitter" in path:
            for operation in ("op", "setOp"):
                anchor = f"bce_->emitAtomOp({operation}, propAtomIndex_)"
                if text.count(anchor) != 2:
                    raise ValueError("Engine property source anchor changed; port the patch first")
                text = text.replace(anchor, f"bce_->emitAtomOp({operation}, propAtomIndex_, camoufoxSourceCoord_)")
        else:
            for operation in ("op", "setOp"):
                anchor = f"bce_->emitElemOpBase({operation})"
                if text.count(anchor) != 2:
                    raise ValueError("Engine element source anchor changed; port the patch first")
                text = text.replace(anchor, f"bce_->emitElemOpBase({operation}, camoufoxSourceCoord_)")
    elif path == "js/src/vm/SelfHosting.cpp":
        anchor = '#include "vm/Compression.h"'
        text = replace_once(text, anchor, '#include "vm/CamoufoxMonitor.h"\n' + anchor)
        anchor = "static const JSFunctionSpec intrinsic_functions[] = {\n"
        text = replace_once(text, anchor, INTRINSIC + anchor)
        anchor = '    JS_FN("CanBeHeldWeakly", intrinsic_CanBeHeldWeakly, 1, 0),\n'
        text = replace_once(text, anchor,
                            '    JS_FN("CamoufoxObserve", intrinsic_CamoufoxObserve, 4, 0),\n' + anchor)
    elif path == "js/src/builtin/Reflect.js":
        anchor = '  // Steps 2-3 are identical to the runtime semantics of the "in" operator.\n'
        text = replace_once(text, anchor,
                            '  CamoufoxObserve(2, target, propertyKey, undefined);\n\n' + anchor)
        anchor = "  // Step 3 (reordered).\n"
        text = replace_once(text, anchor,
                            '  CamoufoxObserve(0, target, propertyKey, undefined);\n\n' + anchor)
    elif path == "js/src/builtin/Reflect.cpp":
        anchor = '#include "jsapi.h"\n'
        text = replace_once(text, anchor, anchor + '#include "js/Exception.h"\n')
        anchor = '#include "vm/GlobalObject.h"'
        text = replace_once(text, anchor, '#include "vm/CamoufoxMonitor.h"\n' + anchor)
        anchor = '#include "vm/JSObject-inl.h"'
        text = replace_once(text, anchor, '#include "vm/JSAtomUtils-inl.h"\n' + anchor)
        anchor = "  RootedValue value(cx, args.get(2));\n"
        text = replace_once(text, anchor, anchor + '''  if (CamoufoxMonitorEnabled()) {
    JS::AutoSaveExceptionState saved(cx);
    RootedValue observedKey(cx, IdToValue(key));
    bool available = true;
    if (key.isInt()) {
      RootedString string(cx, IdToString(cx, key));
      if (string) {
        observedKey.setString(string);
      } else {
        available = false;
        CamoufoxMonitorFailure("engine_reflect_key");
      }
    }
    if (available) {
      CamoufoxMonitorProperty(cx, 1, args.get(0), observedKey, value);
    }
    saved.restore();
  }
''')
    elif path == "js/src/frontend/StencilXdr.cpp":
        anchor = '#include "vm/JSScript.h"'
        text = replace_once(text, anchor, '#include "vm/CamoufoxMonitor.h"\n' + anchor)
        anchor = "  if (!buildId->reserve(buildId->length() + 4)) {\n"
        text = replace_once(text, anchor, '''  const bool observed = js::CamoufoxMonitorEnabled();
  if (!buildId->reserve(buildId->length() + 4 + (observed ? 5 : 0))) {
''')
        anchor = "    buildId->infallibleAppend('b');\n  }\n\n  return true;\n"
        text = replace_once(text, anchor, '''    buildId->infallibleAppend('b');
  }
  if (observed) {
    buildId->infallibleAppend('-');
    buildId->infallibleAppend('c');
    buildId->infallibleAppend('f');
    buildId->infallibleAppend('m');
    buildId->infallibleAppend('2');
  }

  return true;
''')
    else:
        raise ValueError(f"Unsupported engine source path: {path}")
    return text.encode("utf-8")
