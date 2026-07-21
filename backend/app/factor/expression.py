"""Safe factor expression evaluator.

Parses and evaluates Qlib-style factor expressions without using eval().
Supports arithmetic operations, variable resolution ($close, pe_ttm, etc.),
and MyTT function calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

import numpy as np

from app.libs import MyTT


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

class TokenType(Enum):
    NUMBER = auto()
    IDENT = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    LPAREN = auto()
    RPAREN = auto()
    COMMA = auto()
    DOT = auto()
    EOF = auto()


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: str
    pos: int


_KEYWORDS = frozenset({
    "ABS", "LN", "POW", "SQRT", "SIN", "COS", "TAN",
    "MAX", "MIN", "IF", "MA", "EMA", "RSI", "MACD", "KDJ", "BOLL",
    "STD", "REF", "DIFF", "SUM", "HHV", "LLV", "BARSLAST",
    "MyTT",
})

_VAR_ALIASES = {
    "PE_TTM": "pe_ttm",
    "PB": "pb",
    "ROE": "roe",
    "REVENUE_GROWTH": "revenue_growth",
    "pe_ttm": "pe_ttm",
    "pb": "pb",
    "roe": "roe",
    "revenue_growth": "revenue_growth",
    "fundamental_roe": "roe",
    "fundamental.pe_ttm": "pe_ttm",
    "fundamental.pb": "pb",
    "fundamental.roe": "roe",
    "fundamental.revenue_growth": "revenue_growth",
}

_KLINE_VARS = {"$close", "$open", "$high", "$low", "$volume", "$amount"}
_FUNDAMENTAL_VARS = {"pe_ttm", "pb", "roe", "revenue_growth"}
_ALLOWED_VARS = _KLINE_VARS | _FUNDAMENTAL_VARS

_TOKEN_SPEC = [
    ("NUMBER", r"\d+\.?\d*"),
    ("IDENT", r"[A-Za-z_$][A-Za-z0-9_.$]*"),
    ("PLUS", r"\+"),
    ("MINUS", r"-"),
    ("STAR", r"\*"),
    ("SLASH", r"/"),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("COMMA", r","),
    ("DOT", r"\."),
    ("SKIP", r"[ \t]+"),
    ("NEWLINE", r"\n"),
]
_TOKEN_RE = re.compile("|".join(f"(?P<{name}>{pattern})" for name, pattern in _TOKEN_SPEC))


def tokenize(expr: str) -> list[Token]:
    tokens: list[Token] = []
    for m in _TOKEN_RE.finditer(expr):
        kind = m.lastgroup
        value = m.group()
        if kind == "SKIP" or kind == "NEWLINE":
            continue
        if kind == "NUMBER":
            tokens.append(Token(TokenType.NUMBER, value, m.start()))
        elif kind == "IDENT":
            tokens.append(Token(TokenType.IDENT, value, m.start()))
        elif kind == "PLUS":
            tokens.append(Token(TokenType.PLUS, value, m.start()))
        elif kind == "MINUS":
            tokens.append(Token(TokenType.MINUS, value, m.start()))
        elif kind == "STAR":
            tokens.append(Token(TokenType.STAR, value, m.start()))
        elif kind == "SLASH":
            tokens.append(Token(TokenType.SLASH, value, m.start()))
        elif kind == "LPAREN":
            tokens.append(Token(TokenType.LPAREN, value, m.start()))
        elif kind == "RPAREN":
            tokens.append(Token(TokenType.RPAREN, value, m.start()))
        elif kind == "COMMA":
            tokens.append(Token(TokenType.COMMA, value, m.start()))
        elif kind == "DOT":
            tokens.append(Token(TokenType.DOT, value, m.start()))
    tokens.append(Token(TokenType.EOF, "", len(expr)))
    return tokens


# ---------------------------------------------------------------------------
# AST Nodes
# ---------------------------------------------------------------------------

class ASTNode:
    def evaluate(self, ctx: FactorContext) -> np.ndarray:
        raise NotImplementedError


@dataclass(frozen=True)
class NumberLiteral(ASTNode):
    value: float

    def evaluate(self, ctx: FactorContext) -> np.ndarray:
        return np.full(ctx.length, self.value, dtype=np.float64)


@dataclass(frozen=True)
class Variable(ASTNode):
    name: str

    def evaluate(self, ctx: FactorContext) -> np.ndarray:
        if self.name in _KLINE_VARS:
            arr = ctx.kline.get(self.name)
            if arr is None:
                raise ValueError(f"variable {self.name} not available in context")
            return arr.astype(np.float64)
        canonical = _VAR_ALIASES.get(self.name, self.name)
        if canonical in _FUNDAMENTAL_VARS:
            scalar = ctx.fundamentals.get(canonical)
            if scalar is None:
                return np.full(ctx.length, np.nan, dtype=np.float64)
            return np.full(ctx.length, float(scalar), dtype=np.float64)
        raise ValueError(f"unknown variable: {self.name}")


@dataclass(frozen=True)
class BinaryOp(ASTNode):
    op: str
    left: ASTNode
    right: ASTNode

    def evaluate(self, ctx: FactorContext) -> np.ndarray:
        l = self.left.evaluate(ctx)
        r = self.right.evaluate(ctx)
        if self.op == "+":
            return l + r
        elif self.op == "-":
            return l - r
        elif self.op == "*":
            return l * r
        elif self.op == "/":
            with np.errstate(divide="ignore", invalid="ignore"):
                result = np.where(r != 0, l / r, np.nan)
            return result
        raise ValueError(f"unknown operator: {self.op}")


@dataclass(frozen=True)
class UnaryOp(ASTNode):
    op: str
    operand: ASTNode

    def evaluate(self, ctx: FactorContext) -> np.ndarray:
        val = self.operand.evaluate(ctx)
        if self.op == "-":
            return -val
        return val


@dataclass(frozen=True)
class FunctionCall(ASTNode):
    name: str
    args: tuple[ASTNode, ...]

    def evaluate(self, ctx: FactorContext) -> np.ndarray:
        func_name = self.name.upper()
        if func_name.startswith("MYTT."):
            func_name = func_name[5:]

        if func_name in _MYTT_FUNCTIONS:
            return _MYTT_FUNCTIONS[func_name](ctx, self.args)
        if func_name in _MATH_FUNCTIONS:
            return _MATH_FUNCTIONS[func_name](ctx, self.args)
        raise ValueError(f"unknown function: {self.name}")


# ---------------------------------------------------------------------------
# Parser (recursive descent)
# ---------------------------------------------------------------------------

class ParseError(Exception):
    def __init__(self, message: str, pos: int = 0):
        super().__init__(message)
        self.pos = pos


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return tok

    def expect(self, tt: TokenType) -> Token:
        tok = self.peek()
        if tok.type != tt:
            raise ParseError(f"expected {tt.name}, got {tok.type.name} ('{tok.value}')", tok.pos)
        return self.advance()

    def parse(self) -> ASTNode:
        node = self.parse_additive()
        if self.peek().type != TokenType.EOF:
            raise ParseError(f"unexpected token: '{self.peek().value}'", self.peek().pos)
        return node

    def parse_additive(self) -> ASTNode:
        left = self.parse_multiplicative()
        while self.peek().type in (TokenType.PLUS, TokenType.MINUS):
            op_tok = self.advance()
            right = self.parse_multiplicative()
            left = BinaryOp(op_tok.value, left, right)
        return left

    def parse_multiplicative(self) -> ASTNode:
        left = self.parse_unary()
        while self.peek().type in (TokenType.STAR, TokenType.SLASH):
            op_tok = self.advance()
            right = self.parse_unary()
            left = BinaryOp(op_tok.value, left, right)
        return left

    def parse_unary(self) -> ASTNode:
        if self.peek().type == TokenType.MINUS:
            op_tok = self.advance()
            operand = self.parse_unary()
            return UnaryOp("-", operand)
        if self.peek().type == TokenType.PLUS:
            self.advance()
            return self.parse_unary()
        return self.parse_primary()

    def parse_primary(self) -> ASTNode:
        tok = self.peek()

        if tok.type == TokenType.NUMBER:
            self.advance()
            return NumberLiteral(float(tok.value))

        if tok.type == TokenType.IDENT:
            self.advance()
            name = tok.value
            if self.peek().type == TokenType.LPAREN:
                return self.parse_function_call(name)
            return Variable(name)

        if tok.type == TokenType.LPAREN:
            self.advance()
            node = self.parse_additive()
            self.expect(TokenType.RPAREN)
            return node

        raise ParseError(f"unexpected token: '{tok.value}'", tok.pos)

    def parse_function_call(self, name: str) -> ASTNode:
        self.expect(TokenType.LPAREN)
        args: list[ASTNode] = []
        if self.peek().type != TokenType.RPAREN:
            args.append(self.parse_additive())
            while self.peek().type == TokenType.COMMA:
                self.advance()
                args.append(self.parse_additive())
        self.expect(TokenType.RPAREN)
        return FunctionCall(name, tuple(args))


def parse(expr: str) -> ASTNode:
    tokens = tokenize(expr)
    parser = _Parser(tokens)
    return parser.parse()


# ---------------------------------------------------------------------------
# Evaluation context
# ---------------------------------------------------------------------------

@dataclass
class FactorContext:
    kline: dict[str, np.ndarray] = field(default_factory=dict)
    fundamentals: dict[str, Any] = field(default_factory=dict)
    length: int = 0


def evaluate_expression(expr: str, ctx: FactorContext) -> np.ndarray:
    ast = parse(expr)
    return ast.evaluate(ctx)


# ---------------------------------------------------------------------------
# Built-in function implementations
# ---------------------------------------------------------------------------

def _resolve_arr(ctx: FactorContext, node: ASTNode) -> np.ndarray:
    return node.evaluate(ctx).astype(np.float64)


def _resolve_int(node: ASTNode, ctx: FactorContext | None = None) -> int:
    if isinstance(node, NumberLiteral):
        return int(node.value)
    raise ValueError("expected integer argument")


def _resolve_float(node: ASTNode, ctx: FactorContext | None = None) -> float:
    if isinstance(node, NumberLiteral):
        return node.value
    raise ValueError("expected numeric argument")


def _mytt_ma(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    s = _resolve_arr(ctx, args[0])
    n = _resolve_int(args[1])
    return MyTT.MA(s, n)


def _mytt_ema(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    s = _resolve_arr(ctx, args[0])
    n = _resolve_int(args[1])
    return MyTT.EMA(s, n)


def _mytt_rsi(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    s = _resolve_arr(ctx, args[0])
    n = _resolve_int(args[1])
    return MyTT.RSI(s, n)


def _mytt_std(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    s = _resolve_arr(ctx, args[0])
    n = _resolve_int(args[1])
    return MyTT.STD(s, n)


def _mytt_ref(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    s = _resolve_arr(ctx, args[0])
    n = _resolve_int(args[1])
    return MyTT.REF(s, n)


def _mytt_diff(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    s = _resolve_arr(ctx, args[0])
    n = _resolve_int(args[1]) if len(args) > 1 else 1
    return MyTT.DIFF(s, n)


def _mytt_sum(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    s = _resolve_arr(ctx, args[0])
    n = _resolve_int(args[1])
    return MyTT.SUM(s, n)


def _mytt_hhv(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    s = _resolve_arr(ctx, args[0])
    n = _resolve_int(args[1])
    return MyTT.HHV(s, n)


def _mytt_llv(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    s = _resolve_arr(ctx, args[0])
    n = _resolve_int(args[1])
    return MyTT.LLV(s, n)


def _mytt_max2(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    a = _resolve_arr(ctx, args[0])
    b = _resolve_arr(ctx, args[1])
    return MyTT.MAX(a, b)


def _mytt_min2(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    a = _resolve_arr(ctx, args[0])
    b = _resolve_arr(ctx, args[1])
    return MyTT.MIN(a, b)


def _mytt_if(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    s = _resolve_arr(ctx, args[0])
    a = _resolve_arr(ctx, args[1])
    b = _resolve_arr(ctx, args[2])
    return MyTT.IF(s, a, b)


_MYTT_FUNCTIONS: dict[str, Any] = {
    "MA": _mytt_ma,
    "EMA": _mytt_ema,
    "RSI": _mytt_rsi,
    "STD": _mytt_std,
    "REF": _mytt_ref,
    "DIFF": _mytt_diff,
    "SUM": _mytt_sum,
    "HHV": _mytt_hhv,
    "LLV": _mytt_llv,
    "MAX": _mytt_max2,
    "MIN": _mytt_min2,
    "IF": _mytt_if,
}


def _math_abs(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    return np.abs(_resolve_arr(ctx, args[0]))


def _math_pow(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    s = _resolve_arr(ctx, args[0])
    n = _resolve_float(args[1])
    return np.power(s, n)


def _math_sqrt(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    return np.sqrt(_resolve_arr(ctx, args[0]))


def _math_ln(ctx: FactorContext, args: tuple[ASTNode, ...]) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log(_resolve_arr(ctx, args[0]))


_MATH_FUNCTIONS: dict[str, Any] = {
    "ABS": _math_abs,
    "POW": _math_pow,
    "SQRT": _math_sqrt,
    "LN": _math_ln,
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_expression(expr: str) -> tuple[bool, str | None]:
    try:
        tokens = tokenize(expr)
        parser = _Parser(tokens)
        ast = parser.parse()
    except ParseError as e:
        return False, str(e)
    except Exception as e:
        return False, f"parse error: {e}"

    try:
        _check_nodes(ast)
    except ValueError as e:
        return False, str(e)

    return True, None


def _check_nodes(node: ASTNode) -> None:
    if isinstance(node, Variable):
        canonical = _VAR_ALIASES.get(node.name, node.name)
        if canonical not in _ALLOWED_VARS:
            raise ValueError(f"unknown variable: {node.name}")
    elif isinstance(node, FunctionCall):
        func_name = node.name.upper()
        if func_name.startswith("MYTT."):
            func_name = func_name[5:]
        if func_name not in _MYTT_FUNCTIONS and func_name not in _MATH_FUNCTIONS:
            raise ValueError(f"unknown function: {node.name}")
        for arg in node.args:
            _check_nodes(arg)
    elif isinstance(node, BinaryOp):
        _check_nodes(node.left)
        _check_nodes(node.right)
    elif isinstance(node, UnaryOp):
        _check_nodes(node.operand)
