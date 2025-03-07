#  Copyright (c) 2016-2017, 2019-2023 Rocky Bernstein
"""
Python 3.7 base code. We keep non-custom-generated grammar rules out of this file.
"""
from spark_parser import DEFAULT_DEBUG as PARSER_DEFAULT_DEBUG
from spark_parser.spark import rule2str

from decompyle3.parsers.parse_heads import ParserError, PythonBaseParser, nop_func
from decompyle3.parsers.reduce_check import (
    and_cond_check,
    and_invalid,
    and_not_check,
    c_tryelsestmt,
    if_and_elsestmt,
    if_and_stmt,
    ifelsestmt,
    iflaststmt,
    ifstmt,
    ifstmts_jump,
    lastc_stmt,
    list_if_not,
    not_or_check,
    or_check,
    or_cond_check,
    testtrue,
    tryexcept,
    while1elsestmt,
    while1stmt,
    whilestmt,
)
from decompyle3.parsers.treenode import SyntaxTree


class Python37BaseParser(PythonBaseParser):
    def __init__(self, debug_parser=PARSER_DEFAULT_DEBUG):

        self.added_rules = set()
        super(Python37BaseParser, self).__init__(SyntaxTree, debug=debug_parser)
        self.new_rules = set()

    @staticmethod
    def call_fn_name(token):
        """Customize CALL_FUNCTION to add the number of positional arguments"""
        if token.attr is not None:
            return "%s_%i" % (token.kind, token.attr)
        else:
            return "%s_0" % (token.kind)

    def add_make_function_rule(self, rule, opname, attr, customize):
        """Python 3.3 added a an additional LOAD_STR before MAKE_FUNCTION and
        this has an effect on many rules.
        """
        new_rule = rule % "LOAD_STR "
        self.add_unique_rule(new_rule, opname, attr, customize)

    def custom_build_class_rule(self, opname, i, token, tokens, customize):
        """
        # Should the first rule be somehow folded into the 2nd one?
        build_class ::= LOAD_BUILD_CLASS mkfunc
                        LOAD_CLASSNAME {expr}^n-1 CALL_FUNCTION_n
                        LOAD_CONST CALL_FUNCTION_n
        build_class ::= LOAD_BUILD_CLASS mkfunc
                        expr
                        call
                        CALL_FUNCTION_3
        """
        # FIXME: I bet this can be simplified
        # look for next MAKE_FUNCTION
        for i in range(i + 1, len(tokens)):
            if tokens[i].kind.startswith("MAKE_FUNCTION"):
                break
            elif tokens[i].kind.startswith("MAKE_CLOSURE"):
                break
            pass
        assert i < len(
            tokens
        ), "build_class needs to find MAKE_FUNCTION or MAKE_CLOSURE"
        assert (
            tokens[i + 1].kind == "LOAD_STR"
        ), "build_class expecting CONST after MAKE_FUNCTION/MAKE_CLOSURE"
        call_fn_tok = None
        for i in range(i, len(tokens)):
            if tokens[i].kind.startswith("CALL_FUNCTION"):
                call_fn_tok = tokens[i]
                break
        if not call_fn_tok:
            raise RuntimeError(
                "build_class custom rule for %s needs to find CALL_FUNCTION" % opname
            )

        # customize build_class rule
        # FIXME: What's the deal with the two rules? Different Python versions?
        # Different situations? Note that the above rule is based on the CALL_FUNCTION
        # token found, while this one doesn't.
        # 3.6+ handling
        call_function = call_fn_tok.kind
        if call_function.startswith("CALL_FUNCTION_KW"):
            self.addRule("classdef ::= build_class_kw store", nop_func)
            rule = "build_class_kw ::= LOAD_BUILD_CLASS mkfunc %sLOAD_CONST %s" % (
                "expr " * (call_fn_tok.attr - 1),
                call_function,
            )
        else:
            call_function = self.call_fn_name(call_fn_tok)
            rule = "build_class ::= LOAD_BUILD_CLASS mkfunc %s%s" % (
                "expr " * (call_fn_tok.attr - 1),
                call_function,
            )
        self.addRule(rule, nop_func)
        return

    # FIXME FIXME FIXME: The below is an utter mess. Come up with a better
    # organization for this. For example, arrange organize by opcode base?

    def customize_grammar_rules37(self, tokens, customize):
        is_pypy = False

        # For a rough break out on the first word. This may
        # include instructions that don't need customization,
        # but we'll do a finer check after the rough breakout.
        customize_instruction_basenames = frozenset(
            (
                "BEFORE",
                "BUILD",
                "CALL",
                "CONTINUE",
                "DELETE",
                "FORMAT",
                "GET",
                "JUMP",
                "LOAD",
                "LOOKUP",
                "MAKE",
                "RETURN",
                "RAISE",
                "SETUP",
                "UNPACK",
                "WITH",
            )
        )

        # Opcode names in the custom_ops_processed set have rules that get added
        # unconditionally and the rules are constant. So they need to be done
        # only once and if we see the opcode a second we don't have to consider
        # adding more rules.
        #
        # Note: BUILD_TUPLE_UNPACK_WITH_CALL gets considered by
        # default because it starts with BUILD. So we'll set to ignore it from
        # the start.
        custom_ops_processed = set(("BUILD_TUPLE_UNPACK_WITH_CALL",))

        # A set of instruction operation names that exist in the token stream.
        # We use this customize the grammar that we create.
        # 2.6-compatible set comprehensions
        self.seen_ops = frozenset([t.kind for t in tokens])
        self.seen_op_basenames = frozenset(
            [opname[: opname.rfind("_")] for opname in self.seen_ops]
        )

        # Loop over instructions adding custom grammar rules based on
        # a specific instruction seen.

        if "PyPy" in customize:
            is_pypy = True
            self.addRule(
                """
              stmt ::= assign3_pypy
              stmt ::= assign2_pypy
              assign3_pypy       ::= expr expr expr store store store
              assign2_pypy       ::= expr expr store store
              """,
                nop_func,
            )

        n = len(tokens)

        # Determine if we have an iteration CALL_FUNCTION_1.
        has_get_iter_call_function1 = False
        for i, token in enumerate(tokens):
            if (
                token == "GET_ITER"
                and i < n - 2
                and self.call_fn_name(tokens[i + 1]) == "CALL_FUNCTION_1"
            ):
                has_get_iter_call_function1 = True

        for i, token in enumerate(tokens):
            opname = token.kind

            # Do a quick breakout before testing potentially
            # each of the dozen or so instruction in if elif.
            if (
                opname[: opname.find("_")] not in customize_instruction_basenames
                or opname in custom_ops_processed
            ):
                continue

            opname_base = opname[: opname.rfind("_")]

            # The order of opname listed is roughly sorted below

            if opname == "LOAD_ASSERT" and "PyPy" in customize:
                rules_str = """
                stmt ::= JUMP_IF_NOT_DEBUG stmts COME_FROM
                """
                self.add_unique_doc_rules(rules_str, customize)

            elif opname == "BEFORE_ASYNC_WITH":
                rules_str = """
                   stmt            ::= async_with_stmt
                   stmt            ::= async_with_as_stmt
                   c_stmt          ::= c_async_with_stmt
                """

                if self.version < (3, 8):
                    rules_str += """
                      stmt                 ::= async_with_stmt SETUP_ASYNC_WITH
                      c_stmt               ::= c_async_with_stmt SETUP_ASYNC_WITH
                      async_with_stmt      ::= expr
                                               async_with_pre
                                               POP_TOP
                                               suite_stmts_opt
                                               POP_BLOCK LOAD_CONST
                                               async_with_post
                      c_async_with_stmt    ::= expr
                                               async_with_pre
                                               POP_TOP
                                               c_suite_stmts_opt
                                               POP_BLOCK LOAD_CONST
                                               async_with_post
                      async_with_stmt      ::= expr
                                               async_with_pre
                                               POP_TOP
                                               suite_stmts_opt
                                               async_with_post
                      c_async_with_stmt    ::= expr
                                               async_with_pre
                                               POP_TOP
                                               c_suite_stmts_opt
                                               async_with_post
                      async_with_as_stmt   ::= expr
                                               async_with_pre
                                               store
                                               suite_stmts_opt
                                               POP_BLOCK LOAD_CONST
                                               async_with_post
                      c_async_with_as_stmt ::= expr
                                              async_with_pre
                                              store
                                              c_suite_stmts_opt
                                              POP_BLOCK LOAD_CONST
                                              async_with_post
                      async_with_as_stmt   ::= expr
                                              async_with_pre
                                              store
                                              suite_stmts_opt
                                              async_with_post
                      c_async_with_as_stmt ::= expr
                                              async_with_pre
                                              store
                                              suite_stmts_opt
                                              async_with_post
                    """
                else:
                    rules_str += """
                      async_with_pre       ::= BEFORE_ASYNC_WITH GET_AWAITABLE LOAD_CONST YIELD_FROM SETUP_ASYNC_WITH
                      async_with_post      ::= BEGIN_FINALLY COME_FROM_ASYNC_WITH
                                               WITH_CLEANUP_START GET_AWAITABLE LOAD_CONST YIELD_FROM
                                               WITH_CLEANUP_FINISH END_FINALLY
                      async_with_stmt      ::= expr
                                               async_with_pre
                                               POP_TOP
                                               suite_stmts
                                               POP_TOP POP_BLOCK
                                               async_with_post
                      c_async_with_stmt    ::= expr
                                               async_with_pre
                                               POP_TOP
                                               c_suite_stmts
                                               POP_TOP POP_BLOCK
                                               async_with_post
                      async_with_stmt      ::= expr
                                               async_with_pre
                                               POP_TOP
                                               suite_stmts
                                               POP_BLOCK
                                               BEGIN_FINALLY
                                               WITH_CLEANUP_START GET_AWAITABLE LOAD_CONST YIELD_FROM
                                               WITH_CLEANUP_FINISH POP_FINALLY LOAD_CONST RETURN_VALUE
                                               COME_FROM_ASYNC_WITH
                                               WITH_CLEANUP_START GET_AWAITABLE LOAD_CONST YIELD_FROM
                                               WITH_CLEANUP_FINISH END_FINALLY
                      c_async_with_stmt   ::= async_with_stmt
                      async_with_as_stmt   ::= expr
                                               async_with_pre
                                               store suite_stmts
                                               POP_TOP POP_BLOCK
                                               async_with_post
                      c_async_with_as_stmt ::= expr
                                               async_with_pre
                                               store suite_stmts
                                               POP_TOP POP_BLOCK
                                               async_with_post
                      async_with_as_stmt   ::= expr
                                               async_with_pre
                                               store suite_stmts
                                               POP_BLOCK async_with_post
                      c_async_with_as_stmt ::= expr
                                               async_with_pre
                                               store suite_stmts
                                               POP_BLOCK async_with_post
                    """
                self.addRule(rules_str, nop_func)

            elif opname_base == "BUILD_CONST_KEY_MAP":
                kvlist_n = "expr " * (token.attr)
                rule = """
                   expr ::= dict
                   dict ::= %sLOAD_CONST %s
                """ % (
                    kvlist_n,
                    opname,
                )
                self.addRule(rule, nop_func)

            elif opname.startswith("BUILD_LIST_UNPACK"):
                v = token.attr
                rule = "build_list_unpack ::= %s%s" % ("expr " * v, opname)
                self.addRule(rule, nop_func)
                rule = "expr ::= build_list_unpack"
                self.addRule(rule, nop_func)

            elif opname_base in ("BUILD_MAP", "BUILD_MAP_UNPACK"):

                if opname == "BUILD_MAP_UNPACK":
                    self.addRule(
                        """
                        expr        ::= dict_unpack
                        dict_unpack ::= expr BUILD_MAP_UNPACK
                        """,
                        nop_func,
                    )
                    pass
                elif opname.startswith("BUILD_MAP_UNPACK_WITH_CALL"):
                    v = token.attr
                    rule = "build_map_unpack_with_call ::= %s%s" % ("expr " * v, opname)
                    self.addRule(rule, nop_func)

                kvlist_n = "kvlist_%s" % token.attr
                if opname == "BUILD_MAP_n":
                    # PyPy sometimes has no count. Sigh.
                    rule = (
                        "dict_comp_func ::= BUILD_MAP_n LOAD_ARG for_iter store "
                        "comp_iter JUMP_LOOP RETURN_VALUE RETURN_LAST"
                    )
                    self.add_unique_rule(rule, "dict_comp_func", 1, customize)

                    kvlist_n = "kvlist_n"
                    rule = "kvlist_n ::=  kvlist_n kv3"
                    self.add_unique_rule(rule, "kvlist_n", 0, customize)
                    rule = "kvlist_n ::="
                    self.add_unique_rule(rule, "kvlist_n", 1, customize)
                    rule = """
                       expr ::= dict
                       dict ::=  BUILD_MAP_n kvlist_n
                    """

                if not opname.startswith("BUILD_MAP_WITH_CALL"):
                    # FIXME: Use the attr
                    # so this doesn't run into exponential parsing time.
                    if opname.startswith("BUILD_MAP_UNPACK"):
                        # FIXME: start here. The LHS should be dict_unpack, not dict.
                        # FIXME: really we need a combination of dict_entry-like things.
                        # It just so happens the most common case is not to mix
                        # dictionary comphensions with dictionary, elements
                        if "LOAD_DICTCOMP" in self.seen_ops:
                            rule = """
                               expr ::= dict_comp
                               expr ::= dict
                               dict ::= %s%s
                            """ % (
                                "dict_comp " * token.attr,
                                opname,
                            )
                            self.addRule(rule, nop_func)
                        rule = """
                         expr        ::= dict_unpack
                         dict_unpack ::= %s%s
                         """ % (
                            "expr " * token.attr,
                            opname,
                        )
                    else:
                        rule = "%s ::= %s %s" % (
                            kvlist_n,
                            "expr " * (token.attr * 2),
                            opname,
                        )
                        self.add_unique_rule(rule, opname, token.attr, customize)
                        rule = (
                            """
                        expr ::= dict
                        dict ::=  %s
                        """
                            % kvlist_n
                        )
                self.add_unique_rule(rule, opname, token.attr, customize)

            elif opname.startswith("BUILD_MAP_UNPACK_WITH_CALL"):
                v = token.attr
                rule = "build_map_unpack_with_call ::= %s%s" % ("expr " * v, opname)
                self.addRule(rule, nop_func)

            elif opname.startswith("BUILD_TUPLE_UNPACK_WITH_CALL"):
                v = token.attr
                rule = (
                    "build_tuple_unpack_with_call ::= "
                    + "expr1024 " * int(v // 1024)
                    + "expr32 " * int((v // 32) % 32)
                    + "expr " * (v % 32)
                    + opname
                )
                self.addRule(rule, nop_func)
                rule = "starred ::= %s %s" % ("expr " * v, opname)
                self.addRule(rule, nop_func)

            elif opname_base in (
                "BUILD_LIST",
                "BUILD_SET",
                "BUILD_TUPLE",
                "BUILD_TUPLE_UNPACK",
            ):
                collection_size = token.attr

                is_LOAD_CLOSURE = False
                if opname_base == "BUILD_TUPLE":
                    # If is part of a "load_closure", then it is not part of a
                    # "list".
                    is_LOAD_CLOSURE = True
                    for j in range(collection_size):
                        if tokens[i - j - 1].kind != "LOAD_CLOSURE":
                            is_LOAD_CLOSURE = False
                            break
                    if is_LOAD_CLOSURE:
                        rule = "load_closure ::= %s%s" % (
                            ("LOAD_CLOSURE " * collection_size),
                            opname,
                        )
                        self.add_unique_rule(rule, opname, token.attr, customize)
                if not is_LOAD_CLOSURE or collection_size == 0:
                    # We do this complicated test to speed up parsing of
                    # pathelogically long literals, especially those over 1024.
                    thousands = collection_size // 1024
                    thirty32s = (collection_size // 32) % 32
                    if thirty32s > 0:
                        rule = "expr32 ::=%s" % (" expr" * 32)
                        self.add_unique_rule(
                            rule, opname_base, collection_size, customize
                        )
                        pass
                    if thousands > 0:
                        self.add_unique_rule(
                            "expr1024 ::=%s" % (" expr32" * 32),
                            opname_base,
                            collection_size,
                            customize,
                        )
                        pass
                    collection = opname_base[opname_base.find("_") + 1 :].lower()
                    rule = (
                        ("%s ::= " % collection)
                        + "expr1024 " * thousands
                        + "expr32 " * thirty32s
                        + "expr " * (collection_size % 32)
                        + opname
                    )
                    self.add_unique_rules(["expr ::= %s" % collection, rule], customize)
                    continue
                continue
            elif opname_base == "BUILD_SLICE":
                if token.attr == 2:
                    self.add_unique_rules(
                        [
                            "expr ::= slice2",
                            "slice2 ::= expr expr BUILD_SLICE_2",
                        ],
                        customize,
                    )
                else:
                    assert token.attr == 3, (
                        "BUILD_SLICE value must be 2 or 3; is %s" % token.attr
                    )
                    self.add_unique_rules(
                        [
                            "expr   ::= slice3",
                            "slice3 ::= expr expr expr BUILD_SLICE_3",
                        ],
                        customize,
                    )

            elif opname.startswith("BUILD_STRING"):
                v = token.attr
                rules_str = """
                    expr                 ::= joined_str
                    joined_str           ::= %sBUILD_STRING_%d
                """ % (
                    "expr " * v,
                    v,
                )
                self.add_unique_doc_rules(rules_str, customize)
                if "FORMAT_VALUE_ATTR" in self.seen_ops:
                    rules_str = """
                      formatted_value_attr ::= expr expr FORMAT_VALUE_ATTR expr BUILD_STRING
                      expr                 ::= formatted_value_attr
                    """
                    self.add_unique_doc_rules(rules_str, customize)

            elif opname in frozenset(
                (
                    "CALL_FUNCTION",
                    "CALL_FUNCTION_EX",
                    "CALL_FUNCTION_EX_KW",
                    "CALL_FUNCTION_VAR",
                    "CALL_FUNCTION_VAR_KW",
                )
            ) or opname.startswith("CALL_FUNCTION_KW"):

                if opname == "CALL_FUNCTION" and token.attr == 1:
                    rule = """
                     expr         ::= dict_comp
                     dict_comp    ::= LOAD_DICTCOMP LOAD_STR MAKE_FUNCTION_0 expr
                                      GET_ITER CALL_FUNCTION_1
                    classdefdeco1 ::= expr classdefdeco2 CALL_FUNCTION_1
                    classdefdeco1 ::= expr classdefdeco1 CALL_FUNCTION_1
                    """
                    self.addRule(rule, nop_func)

                self.custom_classfunc_rule(opname, token, customize, tokens[i + 1])
                # Note: don't add to custom_ops_processed.

            elif opname_base == "CALL_METHOD":
                # PyPy and Python 3.7+ only - DRY with parse2

                if opname == "CALL_METHOD_KW":
                    args_kw = token.attr
                    rules_str = """
                         expr ::= call_kw_pypy37
                         pypy_kw_keys ::= LOAD_CONST
                    """
                    self.add_unique_doc_rules(rules_str, customize)
                    rule = (
                        "call_kw_pypy37 ::= expr "
                        + ("expr " * args_kw)
                        + " pypy_kw_keys "
                        + opname
                    )
                else:
                    args_pos, args_kw = self.get_pos_kw(token)
                    # number of apply equiv arguments:
                    nak = (len(opname_base) - len("CALL_METHOD")) // 3
                    rule = (
                        "call ::= expr "
                        + ("expr " * args_pos)
                        + ("kwarg " * args_kw)
                        + "expr " * nak
                        + opname
                    )

                self.add_unique_rule(rule, opname, token.attr, customize)

            elif opname == "CONTINUE":
                self.addRule("continue ::= CONTINUE", nop_func)
                custom_ops_processed.add(opname)
            elif opname == "CONTINUE_LOOP":
                self.addRule("continue ::= CONTINUE_LOOP", nop_func)
                custom_ops_processed.add(opname)
            elif opname == "DELETE_ATTR":
                self.addRule("delete ::= expr DELETE_ATTR", nop_func)
                custom_ops_processed.add(opname)
            elif opname == "DELETE_DEREF":
                self.addRule(
                    """
                   stmt           ::= del_deref_stmt
                   del_deref_stmt ::= DELETE_DEREF
                   """,
                    nop_func,
                )
                custom_ops_processed.add(opname)
            elif opname == "DELETE_SUBSCR":
                self.addRule(
                    """
                    delete ::= delete_subscript
                    delete_subscript ::= expr expr DELETE_SUBSCR
                   """,
                    nop_func,
                )
                custom_ops_processed.add(opname)

            elif opname == "FORMAT_VALUE":
                rules_str = """
                    expr              ::= formatted_value1
                    formatted_value1  ::= expr FORMAT_VALUE
                """
                self.add_unique_doc_rules(rules_str, customize)

            elif opname == "FORMAT_VALUE_ATTR":
                rules_str = """
                expr              ::= formatted_value2
                formatted_value2  ::= expr expr FORMAT_VALUE_ATTR
                """
                self.add_unique_doc_rules(rules_str, customize)

            elif opname == "GET_AITER":
                self.addRule(
                    """
                    stmt                ::= genexpr_func_async
                   """,
                    nop_func,
                )
                custom_ops_processed.add(opname)
            elif opname == "JUMP_IF_NOT_DEBUG":
                v = token.attr
                self.addRule(
                    """
                    stmt        ::= assert_pypy
                    stmt        ::= assert2_pypy", nop_func)
                    assert_pypy ::=  JUMP_IF_NOT_DEBUG expr POP_JUMP_IF_TRUE
                                     LOAD_ASSERT RAISE_VARARGS_1 COME_FROM
                    assert2_pypy ::= JUMP_IF_NOT_DEBUG assert_expr POP_JUMP_IF_TRUE
                                     LOAD_ASSERT expr CALL_FUNCTION_1
                                     RAISE_VARARGS_1 COME_FROM
                    assert2_pypy ::= JUMP_IF_NOT_DEBUG expr POP_JUMP_IF_TRUE
                                     LOAD_ASSERT expr CALL_FUNCTION_1
                                     RAISE_VARARGS_1 COME_FROM,
                    """,
                    nop_func,
                )
                custom_ops_processed.add(opname)
            elif opname == "LOAD_BUILD_CLASS":
                self.custom_build_class_rule(opname, i, token, tokens, customize)
                # Note: don't add to custom_ops_processed.
            elif opname == "LOAD_CLASSDEREF":
                # Python 3.4+
                self.addRule("expr ::= LOAD_CLASSDEREF", nop_func)
                custom_ops_processed.add(opname)
            elif opname == "LOAD_CLASSNAME":
                self.addRule("expr ::= LOAD_CLASSNAME", nop_func)
                custom_ops_processed.add(opname)
            elif opname == "LOAD_DICTCOMP":
                if has_get_iter_call_function1:
                    rule_pat = (
                        "dict_comp ::= LOAD_DICTCOMP %sMAKE_FUNCTION_0 expr "
                        "GET_ITER CALL_FUNCTION_1"
                    )
                    self.add_make_function_rule(rule_pat, opname, token.attr, customize)
                    pass
                custom_ops_processed.add(opname)
            elif opname == "LOAD_LISTCOMP":
                self.add_unique_rule(
                    "expr ::= list_comp", opname, token.attr, customize
                )
                custom_ops_processed.add(opname)
            elif opname == "LOAD_NAME":
                if (
                    token.attr == "__annotations__"
                    and "SETUP_ANNOTATIONS" in self.seen_ops
                ):
                    token.kind = "LOAD_ANNOTATION"
                    self.addRule(
                        """
                        stmt       ::= SETUP_ANNOTATIONS
                        stmt       ::= ann_assign
                        ann_assign ::= expr LOAD_ANNOTATION LOAD_STR STORE_SUBSCR
                        """,
                        nop_func,
                    )
                    pass
            elif opname == "LOAD_SETCOMP":
                # Should this be generalized and put under MAKE_FUNCTION?
                if has_get_iter_call_function1:
                    self.addRule("expr ::= set_comp", nop_func)
                    rule_pat = (
                        "set_comp ::= LOAD_SETCOMP %sMAKE_FUNCTION_0 expr "
                        "GET_ITER CALL_FUNCTION_1"
                    )
                    self.add_make_function_rule(rule_pat, opname, token.attr, customize)
                    pass
                custom_ops_processed.add(opname)
            elif opname == "LOOKUP_METHOD":
                # A PyPy speciality - DRY with parse3
                self.addRule(
                    """
                             expr      ::= attribute
                             attribute ::= expr LOOKUP_METHOD
                             """,
                    nop_func,
                )
                custom_ops_processed.add(opname)
            elif opname.startswith("MAKE_CLOSURE"):
                # DRY with MAKE_FUNCTION
                # Note: this probably doesn't handle kwargs proprerly

                if opname == "MAKE_CLOSURE_0" and "LOAD_DICTCOMP" in self.seen_ops:
                    # Is there something general going on here?
                    # Note that 3.6+ doesn't do this, but we'll remove
                    # this rule in parse36.py
                    rule = """
                        dict_comp ::= load_closure LOAD_DICTCOMP LOAD_STR
                                      MAKE_CLOSURE_0 expr
                                      GET_ITER CALL_FUNCTION_1
                    """
                    self.addRule(rule, nop_func)

                args_pos, args_kw, annotate_args = token.attr

                # FIXME: Fold test  into add_make_function_rule
                j = 2
                if is_pypy or (i >= j and tokens[i - j] == "LOAD_LAMBDA"):
                    rule_pat = """
                                expr        ::= lambda_body
                                lambda_body ::= %sload_closure LOAD_LAMBDA %%s%s
                               """ % (
                        "expr " * args_pos,
                        opname,
                    )
                    self.add_make_function_rule(rule_pat, opname, token.attr, customize)

                if has_get_iter_call_function1:
                    rule_pat = (
                        "generator_exp ::= %sload_closure load_genexpr %%s%s expr "
                        "GET_ITER CALL_FUNCTION_1" % ("expr " * args_pos, opname)
                    )
                    self.add_make_function_rule(rule_pat, opname, token.attr, customize)

                    if has_get_iter_call_function1:
                        if is_pypy or (i >= j and tokens[i - j] == "LOAD_LISTCOMP"):
                            # In the tokens we saw:
                            #   LOAD_LISTCOMP LOAD_CONST MAKE_FUNCTION (>= 3.3) or
                            #   LOAD_LISTCOMP MAKE_FUNCTION (< 3.3) or
                            #   and have GET_ITER CALL_FUNCTION_1
                            # Todo: For Pypy we need to modify this slightly
                            rule_pat = (
                                "list_comp ::= %sload_closure LOAD_LISTCOMP %%s%s expr "
                                "GET_ITER CALL_FUNCTION_1"
                                % ("expr " * args_pos, opname)
                            )
                            self.add_make_function_rule(
                                rule_pat, opname, token.attr, customize
                            )
                        if is_pypy or (i >= j and tokens[i - j] == "LOAD_SETCOMP"):
                            rule_pat = (
                                "set_comp ::= %sload_closure LOAD_SETCOMP %%s%s expr "
                                "GET_ITER CALL_FUNCTION_1"
                                % ("expr " * args_pos, opname)
                            )
                            self.add_make_function_rule(
                                rule_pat, opname, token.attr, customize
                            )
                        if is_pypy or (i >= j and tokens[i - j] == "LOAD_DICTCOMP"):
                            self.add_unique_rule(
                                "dict_comp ::= %sload_closure LOAD_DICTCOMP %s "
                                "expr GET_ITER CALL_FUNCTION_1"
                                % ("expr " * args_pos, opname),
                                opname,
                                token.attr,
                                customize,
                            )

                if args_kw > 0:
                    kwargs_str = "kwargs "
                else:
                    kwargs_str = ""

                rule = "mkfunc ::= %s%s%s load_closure LOAD_CODE LOAD_STR %s" % (
                    "expr " * args_pos,
                    kwargs_str,
                    "expr " * annotate_args,
                    opname,
                )

                self.add_unique_rule(rule, opname, token.attr, customize)

                if args_kw == 0:
                    rule = "mkfunc ::= %sload_closure load_genexpr %s" % (
                        "expr " * args_pos,
                        opname,
                    )
                    self.add_unique_rule(rule, opname, token.attr, customize)

                pass
            elif opname_base.startswith("MAKE_FUNCTION"):
                args_pos, args_kw, annotate_args, closure = token.attr
                stack_count = args_pos + args_kw + annotate_args
                if closure:
                    if args_pos:
                        rule = """
                             expr        ::= lambda_body
                             lambda_body ::= %s%s%s%s
                             """ % (
                            "expr " * stack_count,
                            "load_closure " * closure,
                            "BUILD_TUPLE_1 LOAD_LAMBDA LOAD_STR ",
                            opname,
                        )
                    else:
                        rule = """
                             expr        ::= lambda_body
                             lambda_body ::= %s%s%s""" % (
                            "load_closure " * closure,
                            "LOAD_LAMBDA LOAD_STR ",
                            opname,
                        )
                    self.add_unique_rule(rule, opname, token.attr, customize)

                else:
                    rule = """
                         expr        ::= lambda_body
                         lambda_body ::= %sLOAD_LAMBDA LOAD_STR %s""" % (
                        ("expr " * stack_count),
                        opname,
                    )
                    self.add_unique_rule(rule, opname, token.attr, customize)

                rule = "mkfunc ::= %s%s%s%s" % (
                    "expr " * stack_count,
                    "load_closure " * closure,
                    "LOAD_CODE LOAD_STR ",
                    opname,
                )
                self.add_unique_rule(rule, opname, token.attr, customize)

                if has_get_iter_call_function1:
                    rule_pat = (
                        "generator_exp ::= %sload_genexpr %%s%s expr "
                        "GET_ITER CALL_FUNCTION_1"
                    ) % ("expr " * args_pos, opname)
                    self.add_make_function_rule(rule_pat, opname, token.attr, customize)
                    rule_pat = """
                           expr          ::= generator_exp
                           generator_exp ::= %sload_closure load_genexpr %%s%s expr
                           GET_ITER CALL_FUNCTION_1""" % (
                        "expr " * args_pos,
                        opname,
                    )
                    self.add_make_function_rule(rule_pat, opname, token.attr, customize)
                    if is_pypy or (i >= 2 and tokens[i - 2] == "LOAD_LISTCOMP"):
                        # 3.6+ sometimes bundles all of the
                        # 'exprs' in the rule above into a
                        # tuple.
                        rule_pat = (
                            "list_comp ::= load_closure LOAD_LISTCOMP %%s%s "
                            "expr GET_ITER CALL_FUNCTION_1" % (opname,)
                        )
                        self.add_make_function_rule(
                            rule_pat, opname, token.attr, customize
                        )
                        rule_pat = (
                            "list_comp ::= %sLOAD_LISTCOMP %%s%s expr "
                            "GET_ITER CALL_FUNCTION_1" % ("expr " * args_pos, opname)
                        )
                        self.add_make_function_rule(
                            rule_pat, opname, token.attr, customize
                        )

                if is_pypy or (i >= 2 and tokens[i - 2] == "LOAD_LAMBDA"):
                    rule_pat = """
                        expr        ::= lambda_body
                        lambda_body ::= %s%sLOAD_LAMBDA %%s%s
                        """ % (
                        ("expr " * args_pos),
                        ("kwarg " * args_kw),
                        opname,
                    )
                    self.add_make_function_rule(rule_pat, opname, token.attr, customize)
                continue

                args_pos, args_kw, annotate_args, closure = token.attr

                j = 2

                if has_get_iter_call_function1:
                    rule_pat = (
                        "generator_exp ::= %sload_genexpr %%s%s expr "
                        "GET_ITER CALL_FUNCTION_1" % ("expr " * args_pos, opname)
                    )
                    self.add_make_function_rule(rule_pat, opname, token.attr, customize)

                    if is_pypy or (i >= j and tokens[i - j] == "LOAD_LISTCOMP"):
                        # In the tokens we saw:
                        #   LOAD_LISTCOMP LOAD_CONST MAKE_FUNCTION (>= 3.3) or
                        #   LOAD_LISTCOMP MAKE_FUNCTION (< 3.3) or
                        #   and have GET_ITER CALL_FUNCTION_1
                        # Todo: For Pypy we need to modify this slightly
                        rule_pat = (
                            "list_comp ::= %sLOAD_LISTCOMP %%s%s expr "
                            "GET_ITER CALL_FUNCTION_1" % ("expr " * args_pos, opname)
                        )
                        self.add_make_function_rule(
                            rule_pat, opname, token.attr, customize
                        )

                # FIXME: Fold test  into add_make_function_rule
                if is_pypy or (i >= j and tokens[i - j] == "LOAD_LAMBDA"):
                    rule_pat = """
                        expr        ::= lambda_body
                        lambda_body ::= %s%sLOAD_LAMBDA %%s%s
                        """ % (
                        ("expr " * args_pos),
                        ("kwarg " * args_kw),
                        opname,
                    )
                    self.add_make_function_rule(rule_pat, opname, token.attr, customize)

                if args_kw == 0:
                    kwargs = "no_kwargs"
                    self.add_unique_rule("no_kwargs ::=", opname, token.attr, customize)
                else:
                    kwargs = "kwargs"

                # positional args before keyword args
                rule = "mkfunc ::= %s%s %s%s" % (
                    "expr " * args_pos,
                    kwargs,
                    "LOAD_CODE LOAD_STR ",
                    opname,
                )
                self.add_unique_rule(rule, opname, token.attr, customize)

            elif opname == "RETURN_VALUE_LAMBDA":
                self.addRule(
                    """
                    return_expr_lambda ::= return_expr RETURN_VALUE_LAMBDA
                    """,
                    nop_func,
                )
                custom_ops_processed.add(opname)
            elif opname == "RAISE_VARARGS_0":
                self.addRule(
                    """
                    stmt        ::= raise_stmt0
                    last_stmt  ::= raise_stmt0
                    raise_stmt0 ::= RAISE_VARARGS_0
                    """,
                    nop_func,
                )
                custom_ops_processed.add(opname)
            elif opname == "RAISE_VARARGS_1":
                self.addRule(
                    """
                    stmt        ::= raise_stmt1
                    last_stmt  ::= raise_stmt1
                    raise_stmt1 ::= expr RAISE_VARARGS_1
                    """,
                    nop_func,
                )
                custom_ops_processed.add(opname)
            elif opname == "RAISE_VARARGS_2":
                self.addRule(
                    """
                    stmt        ::= raise_stmt2
                    last_stmt  ::= raise_stmt2
                    raise_stmt2 ::= expr expr RAISE_VARARGS_2
                    """,
                    nop_func,
                )
                custom_ops_processed.add(opname)

            elif opname == "SETUP_EXCEPT":
                self.addRule(
                    """
                    try_except     ::= SETUP_EXCEPT suite_stmts_opt POP_BLOCK
                                       except_handler opt_come_from_except
                    c_try_except   ::= SETUP_EXCEPT c_suite_stmts POP_BLOCK
                                       c_except_handler opt_come_from_except
                    stmt           ::= tryelsestmt3
                    tryelsestmt3   ::= SETUP_EXCEPT suite_stmts_opt POP_BLOCK
                                       except_handler COME_FROM else_suite
                                       opt_come_from_except

                    tryelsestmt    ::= SETUP_EXCEPT suite_stmts_opt POP_BLOCK
                                       except_handler else_suite come_from_except_clauses

                    tryelsestmt    ::= SETUP_EXCEPT suite_stmts_opt POP_BLOCK
                                       except_handler else_suite come_froms

                    c_stmt         ::= c_tryelsestmt
                    c_tryelsestmt  ::= SETUP_EXCEPT c_suite_stmts POP_BLOCK
                                       c_except_handler
                                       come_any_froms else_suitec
                                       come_from_except_clauses
                    """,
                    nop_func,
                )
                custom_ops_processed.add(opname)

            elif opname == "WITH_CLEANUP_START":
                rules_str = """
                  stmt        ::= with_null
                  with_null   ::= with_suffix
                  with_suffix ::= WITH_CLEANUP_START WITH_CLEANUP_FINISH END_FINALLY
                """
                self.addRule(rules_str, nop_func)
            elif opname == "SETUP_WITH":
                rules_str = """
                  stmt        ::= with
                  stmt        ::= withasstmt
                  c_stmt      ::= c_with

                  c_with      ::= expr SETUP_WITH POP_TOP
                                  c_suite_stmts_opt
                                  COME_FROM_WITH
                                  with_suffix
                  c_with      ::= expr SETUP_WITH POP_TOP
                                  c_suite_stmts_opt
                                  POP_BLOCK LOAD_CONST COME_FROM_WITH
                                  with_suffix

                  with        ::= expr SETUP_WITH POP_TOP
                                  suite_stmts_opt
                                  COME_FROM_WITH
                                  with_suffix

                  withasstmt  ::= expr SETUP_WITH store suite_stmts_opt COME_FROM_WITH
                                  with_suffix

                  with        ::= expr
                                  SETUP_WITH POP_TOP suite_stmts_opt
                                  POP_BLOCK LOAD_CONST COME_FROM_WITH
                                  with_suffix
                  withasstmt  ::= expr
                                  SETUP_WITH store suite_stmts_opt
                                  POP_BLOCK LOAD_CONST COME_FROM_WITH
                                  with_suffix

                  with        ::= expr
                                  SETUP_WITH POP_TOP suite_stmts_opt
                                  POP_BLOCK LOAD_CONST COME_FROM_WITH
                                  with_suffix
                  withasstmt  ::= expr
                                  SETUP_WITH store suite_stmts_opt
                                  POP_BLOCK LOAD_CONST COME_FROM_WITH
                                  with_suffix
                """
                if self.version < (3, 8):
                    rules_str += """
                    with      ::= expr SETUP_WITH POP_TOP suite_stmts_opt POP_BLOCK
                                   LOAD_CONST
                                   with_suffix
                    """
                else:
                    rules_str += """
                     # A return at the end of a withas stmt can be this.
                     # FIXME: should this be a different kind of return?
                     return      ::= return_expr POP_BLOCK
                                     ROT_TWO
                                     BEGIN_FINALLY
                                     WITH_CLEANUP_START
                                     WITH_CLEANUP_FINISH
                                     POP_FINALLY
                                     RETURN_VALUE

                      with       ::= expr
                                     SETUP_WITH POP_TOP suite_stmts_opt
                                     POP_BLOCK LOAD_CONST COME_FROM_WITH
                                     with_suffix


                      withasstmt ::= expr
                                     SETUP_WITH store suite_stmts
                                     POP_BLOCK LOAD_CONST COME_FROM_WITH

                      withasstmt ::= expr
                                     SETUP_WITH store suite_stmts
                                     POP_BLOCK BEGIN_FINALLY COME_FROM_WITH
                                     with_suffix

                      # withasstmt ::= expr SETUP_WITH store suite_stmts
                      #                COME_FROM expr COME_FROM POP_BLOCK ROT_TWO
                      #                BEGIN_FINALLY WITH_CLEANUP_START WITH_CLEANUP_FINISH
                      #                POP_FINALLY RETURN_VALUE COME_FROM_WITH
                      #                WITH_CLEANUP_START WITH_CLEANUP_FINISH END_FINALLY

                      with         ::= expr SETUP_WITH POP_TOP suite_stmts_opt POP_BLOCK
                                       BEGIN_FINALLY COME_FROM_WITH
                                       with_suffix
                    """
                self.addRule(rules_str, nop_func)

            elif opname_base in ("UNPACK_EX",):
                before_count, after_count = token.attr
                rule = (
                    """
                        store  ::= unpack
                        unpack ::= """
                    + opname
                    + " store" * (before_count + after_count + 1)
                )
                self.addRule(rule, nop_func)

            elif opname_base == "UNPACK_SEQUENCE":
                rule = (
                    """
                    store  ::= unpack
                    unpack ::= """
                    + opname
                    + " store" * token.attr
                )
                self.addRule(rule, nop_func)
            pass

        self.reduce_check_table = {
            "ifstmts_jump": ifstmts_jump,
            "and": and_invalid,
            "and_cond": and_cond_check,
            "and_not": and_not_check,
            "if_and_stmt": if_and_stmt,
            "if_and_elsestmtc": if_and_elsestmt,
            "ifelsestmt": ifelsestmt,
            "ifelsestmtc": ifelsestmt,
            "iflaststmt": iflaststmt,
            "iflaststmtc": iflaststmt,
            "if_not_stmtc": ifstmt,
            "ifstmt": ifstmt,
            "ifstmtc": ifstmt,
            "lastc_stmt": lastc_stmt,
            "list_if_not": list_if_not,
            "not_or": not_or_check,
            "or": or_check,
            "or_cond": or_cond_check,
            "testtrue": testtrue,
            "testfalsec": testtrue,
            "while1elsestmt": while1elsestmt,
            "while1stmt": while1stmt,
            "whilestmt": whilestmt,
            "c_tryelsestmt": c_tryelsestmt,
            "c_try_except": tryexcept,
        }

        self.check_reduce["and"] = "AST"
        self.check_reduce["and_cond"] = "AST"
        self.check_reduce["and_not"] = "AST"
        self.check_reduce["annotate_tuple"] = "tokens"
        self.check_reduce["aug_assign1"] = "AST"
        self.check_reduce["aug_assign2"] = "AST"
        self.check_reduce["c_try_except"] = "AST"
        self.check_reduce["c_tryelsestmt"] = "AST"
        self.check_reduce["if_and_stmt"] = "AST"
        self.check_reduce["if_and_elsestmtc"] = "AST"
        self.check_reduce["if_not_stmtc"] = "AST"
        self.check_reduce["ifelsestmt"] = "AST"
        self.check_reduce["ifelsestmtc"] = "AST"
        self.check_reduce["iflaststmt"] = "AST"
        self.check_reduce["iflaststmtc"] = "AST"
        self.check_reduce["ifstmt"] = "AST"
        self.check_reduce["ifstmtc"] = "AST"
        self.check_reduce["ifstmts_jump"] = "AST"
        self.check_reduce["ifstmts_jumpc"] = "AST"
        self.check_reduce["import_as37"] = "tokens"
        self.check_reduce["import_from37"] = "AST"
        self.check_reduce["import_from_as37"] = "tokens"
        self.check_reduce["lastc_stmt"] = "tokens"
        self.check_reduce["list_if_not"] = "AST"
        self.check_reduce["while1elsestmt"] = "tokens"
        self.check_reduce["while1stmt"] = "tokens"
        self.check_reduce["whilestmt"] = "tokens"
        self.check_reduce["not_or"] = "AST"
        self.check_reduce["or"] = "AST"
        self.check_reduce["or_cond"] = "tokens"
        self.check_reduce["testtrue"] = "tokens"
        self.check_reduce["testfalsec"] = "tokens"
        return

    def custom_classfunc_rule(self, opname, token, customize, next_token):
        """
        call ::= expr {expr}^n CALL_FUNCTION_n
        call ::= expr {expr}^n CALL_FUNCTION_VAR_n
        call ::= expr {expr}^n CALL_FUNCTION_VAR_KW_n
        call ::= expr {expr}^n CALL_FUNCTION_KW_n

        classdefdeco2 ::= LOAD_BUILD_CLASS mkfunc {expr}^n-1 CALL_FUNCTION_n
        """
        args_pos, args_kw = self.get_pos_kw(token)

        # Additional exprs for * and ** args:
        #  0 if neither
        #  1 for CALL_FUNCTION_VAR or CALL_FUNCTION_KW
        #  2 for * and ** args (CALL_FUNCTION_VAR_KW).
        # Yes, this computation based on instruction name is a little bit hoaky.
        nak = (len(opname) - len("CALL_FUNCTION")) // 3
        uniq_param = args_kw + args_pos

        if frozenset(("GET_AWAITABLE", "YIELD_FROM")).issubset(self.seen_ops):
            rule = (
                "async_call ::= expr "
                + ("expr " * args_pos)
                + ("kwarg " * args_kw)
                + "expr " * nak
                + token.kind
                + " GET_AWAITABLE LOAD_CONST YIELD_FROM"
            )
            self.add_unique_rule(rule, token.kind, uniq_param, customize)
            self.add_unique_rule(
                "expr ::= async_call", token.kind, uniq_param, customize
            )

        if opname.startswith("CALL_FUNCTION_VAR"):
            token.kind = self.call_fn_name(token)
            if opname.endswith("KW"):
                kw = "expr "
            else:
                kw = ""
            rule = (
                "call ::= expr expr "
                + ("expr " * args_pos)
                + ("kwarg " * args_kw)
                + kw
                + token.kind
            )

            # Note: semantic actions make use of the fact of whether "args_pos"
            # zero or not in creating a template rule.
            self.add_unique_rule(rule, token.kind, args_pos, customize)
        else:
            token.kind = self.call_fn_name(token)
            uniq_param = args_kw + args_pos

            # Note: 3.5+ have subclassed this method; so we don't handle
            # 'CALL_FUNCTION_VAR' or 'CALL_FUNCTION_EX' here.
            rule = (
                "call ::= expr "
                + ("expr " * args_pos)
                + ("kwarg " * args_kw)
                + "expr " * nak
                + token.kind
            )

            self.add_unique_rule(rule, token.kind, uniq_param, customize)

            if "LOAD_BUILD_CLASS" in self.seen_ops:
                if (
                    next_token == "CALL_FUNCTION"
                    and next_token.attr == 1
                    and args_pos > 1
                ):
                    rule = "classdefdeco2 ::= LOAD_BUILD_CLASS mkfunc %s%s_%d" % (
                        ("expr " * (args_pos - 1)),
                        opname,
                        args_pos,
                    )
                    self.add_unique_rule(rule, token.kind, uniq_param, customize)

    def reduce_is_invalid(self, rule, ast, tokens, first, last):
        lhs = rule[0]
        n = len(tokens)
        last = min(last, n - 1)
        fn = self.reduce_check_table.get(lhs, None)
        try:
            if fn:
                return fn(self, lhs, n, rule, ast, tokens, first, last)
        except Exception:
            import sys
            import traceback

            print(
                f"Exception in {fn.__name__} {sys.exc_info()[1]}\n"
                + f"rule: {rule2str(rule)}\n"
                + f"offsets {tokens[first].offset} .. {tokens[last].offset}"
            )
            print(traceback.print_tb(sys.exc_info()[2], -1))
            raise ParserError(tokens[last], tokens[last].off2int(), self.debug["rules"])

        if lhs in ("aug_assign1", "aug_assign2") and ast[0][0] == "and":
            return True
        elif lhs == "annotate_tuple":
            return not isinstance(tokens[first].attr, tuple)
        elif lhs == "import_from37":
            importlist37 = ast[3]
            alias37 = importlist37[0]
            if importlist37 == "importlist37" and alias37 == "alias37":
                store = alias37[1]
                assert store == "store"
                return alias37[0].attr != store[0].attr
            return False
        elif lhs == "import_as37":
            return tokens[first + 1].pattr is not None
        elif lhs == "import_from_as37":
            return tokens[first + 1].pattr is None

        return False
