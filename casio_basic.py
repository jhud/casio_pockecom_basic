#!/usr/bin/env python3
"""
Small Casio FX-730P BASIC interpreter.

Core features implemented:
  * Line numbers 1..9999
  * ":" separated statements
  * Numeric variables A..Z
  * String variables A$..Z$ and $
  * DIM numeric/string arrays, up to 3 dimensions, max 8 arrays
  * Array elements may be used directly in expressions, e.g. C=S(I)+S(J)*2
  * ERASE
  * DEFM expanded-variable mode (compact approximation suitable for D(B)-style code)
  * LET / assignment
  * PRINT / INPUT
  * IF ... THEN line
  * GOTO / GOSUB / RETURN
  * ON expr GOTO / GOSUB
  * FOR / TO / STEP / NEXT
  * DATA / READ / RESTORE
  * CLEAR / VAC
  * REM / END / STOP
  * BEEP
  * Common numeric and string functions

Not emulated:
  cassette/printer hardware, DATA BANK files, exact tokenized-memory accounting,
  exact display timing/formatting, statistics mode, and every error code.

The goal is readable FX-730P compatibility, not hardware emulation.
"""

import math
import random
import re
import sys


def split_outside(text, separators=","):
    """Return [(field, separator_after_field), ...], ignoring separators in quotes/parens."""
    result, buf = [], []
    quoted = False
    depth = 0
    for ch in text:
        if ch == '"':
            quoted = not quoted
            buf.append(ch)
        elif not quoted and ch == "(":
            depth += 1
            buf.append(ch)
        elif not quoted and ch == ")":
            depth -= 1
            buf.append(ch)
        elif not quoted and depth == 0 and ch in separators:
            result.append(("".join(buf).strip(), ch))
            buf = []
        else:
            buf.append(ch)
    result.append(("".join(buf).strip(), None))
    return result


def split_statements(line):
    return [x for x, _ in split_outside(line, ":") if x]


class Array:
    """ Simulate a DIM array. """
    def __init__(self, dims, is_string=False):
        if not 1 <= len(dims) <= 3:
            raise RuntimeError("DIM supports 1 to 3 dimensions")
        if any(d < 0 or d > 255 for d in dims):
            raise RuntimeError("array subscript out of range")
        self.dims = tuple(int(d) for d in dims)
        self.is_string = is_string
        self.data = {}

    def key(self, idx):
        idx = tuple(int(x) for x in idx)
        if len(idx) != len(self.dims):
            raise RuntimeError("wrong number of subscripts")
        # DIM A(10) means elements A(0)..A(10).
        if any(i < 0 or i > hi for i, hi in zip(idx, self.dims)):
            raise RuntimeError("subscript out of range")
        return idx

    def get(self, idx):
        return self.data.get(self.key(idx), "" if self.is_string else 0.0)

    def set(self, idx, value):
        k = self.key(idx)
        if self.is_string:
            # Fixed character variables on this family are short.
            self.data[k] = str(value)[:7]
        else:
            self.data[k] = float(value)


class FX730P:
    """ Root class for the simulator / interpreter. """
    def __init__(self):
        self.num = {chr(c): 0.0 for c in range(ord("A"), ord("Z") + 1)}
        self.string = {chr(c) + "$": "" for c in range(ord("A"), ord("Z") + 1)}
        self.string["$"] = ""

        self.arrays = {}
        self.defm_count = 0
        self.defm = [0.0] * 26

        self.program = []
        self.line_to_pc = {}
        self.gosub_stack = []
        self.for_stack = []

        self.data_items = []          # [(line, raw_item), ...]
        self.data_pos = 0

    def load(self, source):
        physical = {}
        for raw in source.splitlines()[1:]: # first line is the filename, required to be sent before the actual line nubmers begin
            raw = raw.strip()
            if not raw:
                continue
            m = re.match(r"^(\d+)\s*(.*)$", raw)
            if not m:
                raise RuntimeError(f"missing line number: {raw}")
            n = int(m.group(1))
            if not 1 <= n <= 9999:
                raise RuntimeError(f"line number out of range: {n}")
            physical[n] = m.group(2).strip()

        self.program = []
        self.line_to_pc = {}
        self.data_items = []

        for n in sorted(physical):
            self.line_to_pc[n] = len(self.program)
            for stmt in split_statements(physical[n]):
                self.program.append((n, stmt))
                if stmt.upper().startswith("DATA "):
                    for item, _ in split_outside(stmt[5:].strip(), ","):
                        self.data_items.append((n, item))
                if stmt.upper().startswith("REM"):
                    break

        self.data_pos = 0

    # ---------- expressions ----------

    def _arr(self, name, *indices):
        name = name.upper()

        if name in self.arrays:
            return self.arrays[name].get(indices)

        # DEFM compatibility approximation:
        # A(0)=A, B(0)=B, etc.; expanded cells continue after Z.
        if self.defm_count:
            if len(indices) != 1:
                raise RuntimeError("DEFM array requires one subscript")
            i = int(indices[0])
            base = ord(name[0]) - ord("A")
            pos = base + i
            if pos < 0 or pos >= 26 + self.defm_count:
                raise RuntimeError("DEFM subscript out of range")
            if pos < 26:
                return self.num[chr(ord("A") + pos)]
            return self.defm[pos]

        raise RuntimeError(f"array not DIMensioned: {name}")

    def _transform(self, expr, comparison=False):
        # Only rewrite BASIC syntax outside quoted strings.  The old version
        # accidentally changed text such as "A$" inside a string literal.
        parts = re.split(r'("(?:[^"]|"")*")', expr.strip())

        funcs = {
            "ABS","SGN","INT","FRAC","SQR","SIN","COS","TAN","ASN","ACS","ATN",
            "LOG","LN","EXP","FACT","RAN","RND","LEN","VAL","STR","MID","HEX"
        }
        pat = re.compile(r"\b([A-Za-z](?:_s)?)\s*\(([^()]*)\)")

        def transform_code(s):
            s = s.replace("π", "PI").replace("↑", "**").replace("^", "**")
            s = s.replace("<>", "!=")

            # BASIC is case-insensitive.  Normalize string variable names.
            s = re.sub(
                r"\b([A-Z])\$",
                lambda m: m.group(1).upper() + "_s",
                s,
                flags=re.I,
            )
            s = re.sub(r"(?<![A-Za-z0-9_])\$(?![A-Za-z0-9_])", "DOLLAR", s)

            while True:
                changed = False

                def repl(m):
                    nonlocal changed
                    name = m.group(1)
                    raw_name = name[:-2] + "$" if name.lower().endswith("_s") else name
                    if raw_name.upper().rstrip("$") in funcs:
                        return m.group(0).upper()
                    changed = True
                    args = m.group(2).strip()
                    return f'ARR("{raw_name.upper()}"{"," if args else ""}{args})'

                ns = pat.sub(repl, s)
                s = ns
                if not changed:
                    break

            if comparison:
                s = re.sub(r"(?<![<>=!])=(?!=)", "==", s)
            return s

        for i in range(0, len(parts), 2):
            parts[i] = transform_code(parts[i])
        return "".join(parts)

    def condition(self, expr):
        """Evaluate an FX-730P IF condition, including string comparisons."""
        quoted = False
        depth = 0
        i = 0
        operators = ("<>", "<=", ">=", "=", "<", ">")

        while i < len(expr):
            ch = expr[i]
            if ch == '"':
                quoted = not quoted
                i += 1
                continue
            if not quoted:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif depth == 0:
                    for op in operators:
                        if expr.startswith(op, i):
                            left = self.value(expr[:i].strip())
                            right = self.value(expr[i + len(op):].strip())
                            return {
                                "=": left == right,
                                "<>": left != right,
                                "<": left < right,
                                "<=": left <= right,
                                ">": left > right,
                                ">=": left >= right,
                            }[op]
            i += 1

        return bool(self.value(expr))

    def value(self, expr, comparison=False):
        env = {
            "PI": math.pi,
            "ABS": abs,
            "SGN": lambda x: (x > 0) - (x < 0),
            "INT": math.floor,
            "FRAC": lambda x: x - math.floor(x),
            "SQR": math.sqrt,
            "SIN": math.sin,
            "COS": math.cos,
            "TAN": math.tan,
            "ASN": math.asin,
            "ACS": math.acos,
            "ATN": math.atan,
            "LOG": math.log10,
            "LN": math.log,
            "EXP": math.exp,
            "FACT": lambda x: math.factorial(int(x)),
            "RAN": lambda: random.random(),
            "RND": lambda x: round(x),
            "LEN": lambda x: len(str(x)),
            "VAL": lambda x: float(x),
            "STR": lambda x: str(x),
            "MID": lambda s, a, n=None: str(s)[int(a)-1:] if n is None else str(s)[int(a)-1:int(a)-1+int(n)],
            "HEX": lambda x: format(int(x), "X"),
            "ARR": self._arr,
            "DOLLAR": self.string["$"],
        }
        env.update(self.num)
        for k, v in self.string.items():
            if k != "$":
                env[k[0] + "_s"] = v

        try:
            return eval(self._transform(expr, comparison), {"__builtins__": {}}, env)
        except Exception as e:
            raise RuntimeError(f"bad expression: {expr}") from e

    # ---------- variables / arrays ----------

    def parse_target(self, text):
        t = text.strip().upper()

        if t == "$":
            return ("scalar_string", "$", ())

        m = re.match(r"^([A-Z]\$?)\s*(?:\((.*)\))?$", t)
        if not m:
            raise RuntimeError(f"bad variable: {text}")

        name, subs = m.groups()
        if subs is None:
            return ("scalar_string" if name.endswith("$") else "scalar_num", name, ())

        idx = tuple(int(self.value(x)) for x, _ in split_outside(subs, ","))
        return ("array", name, idx)

    def get_target(self, text):
        kind, name, idx = self.parse_target(text)
        if kind == "scalar_num":
            return self.num[name]
        if kind == "scalar_string":
            return self.string[name]
        return self._arr(name, *idx)

    def set_target(self, text, value):
        kind, name, idx = self.parse_target(text)

        if kind == "scalar_num":
            self.num[name] = float(value)
            return

        if kind == "scalar_string":
            limit = 62 if name == "$" else 7
            self.string[name] = str(value)[:limit]
            return

        if name in self.arrays:
            self.arrays[name].set(idx, value)
            return

        if self.defm_count and not name.endswith("$"):
            if len(idx) != 1:
                raise RuntimeError("DEFM array requires one subscript")
            pos = ord(name) - ord("A") + idx[0]
            if not 0 <= pos < 26 + self.defm_count:
                raise RuntimeError("DEFM subscript out of range")
            if pos < 26:
                self.num[chr(ord("A") + pos)] = float(value)
            else:
                self.defm[pos] = float(value)
            return

        raise RuntimeError(f"array not DIMensioned: {name}")

    def assign(self, statement):
        m = re.match(r"^(.+?)\s*=\s*(.+)$", statement)
        if not m:
            raise RuntimeError(f"bad assignment: {statement}")
        lhs, rhs = m.groups()

        kind, _, _ = self.parse_target(lhs)
        if kind == "scalar_string" or lhs.strip().upper().endswith("$") or "$(" in lhs.upper():
            value = self.value(rhs) if not (rhs.strip().startswith('"') and rhs.strip().endswith('"')) else rhs.strip()[1:-1]
        else:
            value = self.value(rhs)
        self.set_target(lhs, value)

    def dim(self, arg):
        specs = [x for x, _ in split_outside(arg, ",")]
        # The comma splitter cannot distinguish dimensions from declarations,
        # so parse declarations with a small scanner.
        specs = []
        buf = ""
        depth = 0
        quoted = False
        for ch in arg + ",":
            if ch == '"':
                quoted = not quoted
            if not quoted:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif ch == "," and depth == 0:
                    if buf.strip():
                        specs.append(buf.strip())
                    buf = ""
                    continue
            buf += ch

        # DIM mode replaces DEFM mode.
        self.defm_count = 0
        self.defm = [0.0] * 26

        for spec in specs:
            m = re.match(r"^([A-Z]\$?)\s*\((.*)\)$", spec, re.I)
            if not m:
                raise RuntimeError(f"bad DIM: {spec}")
            name, ds = m.groups()
            name = name.upper()
            dims = [int(self.value(x)) for x, _ in split_outside(ds, ",")]
            if name not in self.arrays and len(self.arrays) >= 8:
                raise RuntimeError("too many DIM arrays (FX-730P limit is 8)")
            self.arrays[name] = Array(dims, name.endswith("$"))

    def erase(self, arg):
        for item, _ in split_outside(arg, ","):
            name = re.sub(r"\(.*\)$", "", item.strip().upper())
            self.arrays.pop(name, None)

    # ---------- statements ----------

    def do_print(self, arg):
        if not arg.strip():
            print()
            return

        pieces = split_outside(arg, ";,")
        out = ""
        last_sep = None
        for item, sep in pieces:
            if item:
                if item.startswith('"') and item.endswith('"'):
                    val = item[1:-1]
                else:
                    val = self.value(item)
                    if isinstance(val, float) and val.is_integer():
                        val = int(val)
                out += str(val)
            if sep == ",":
                out += " "
            last_sep = sep
        print(out, end="" if last_sep == ";" else "\n")

    def do_input(self, arg):
        prompt = "? "
        rest = arg.strip()
        m = re.match(r'^"([^"]*)"\s*,\s*(.+)$', rest)
        if m:
            prompt, rest = m.groups()

        targets = [x for x, _ in split_outside(rest, ",")]
        for i, target in enumerate(targets):
            raw = input(prompt if i == 0 else "? ")
            kind, _, _ = self.parse_target(target)
            self.set_target(target, raw if kind == "scalar_string" or "$" in target else float(raw))

    def restore(self, target=None):
        if target is None:
            self.data_pos = 0
            return
        line = int(self.value(target))
        for i, (n, _) in enumerate(self.data_items):
            if n >= line:
                self.data_pos = i
                return
        self.data_pos = len(self.data_items)

    def read_data(self, arg):
        for target, _ in split_outside(arg, ","):
            if self.data_pos >= len(self.data_items):
                raise RuntimeError("out of DATA")
            _, raw = self.data_items[self.data_pos]
            self.data_pos += 1
            raw = raw.strip()
            kind, _, _ = self.parse_target(target)
            if raw.startswith('"') and raw.endswith('"'):
                val = raw[1:-1]
            elif kind == "scalar_string" or "$" in target:
                val = raw
            else:
                val = self.value(raw)
            self.set_target(target, val)

    def jump(self, line):
        line = int(line)
        if line not in self.line_to_pc:
            raise RuntimeError(f"no such line: {line}")
        return self.line_to_pc[line]

    def clear_vars(self):
        for k in self.num:
            self.num[k] = 0.0
        for k in self.string:
            self.string[k] = ""
        for arr in self.arrays.values():
            arr.data.clear()
        for i in range(len(self.defm)):
            self.defm[i] = 0.0

    def run(self):
        pc = 0
        step_count = 0
        while pc < len(self.program):
            line, stmt = self.program[pc]
            s = stmt.strip()
            u = s.upper()

            try:
                step_count += 1
                if step_count >= 100000:
                    raise RuntimeError(f"ERROR: possible infinite loop detected at {line}")

                if not s or u.startswith("REM"):
                    pc += 1

                elif u.startswith("DATA"):
                    pc += 1

                elif u in ("END", "STOP"):
                    return

                elif u in ("CLEAR", "VAC"):
                    self.clear_vars()
                    pc += 1

                elif u.startswith("DIM "):
                    self.dim(s[4:].strip())
                    pc += 1

                elif u.startswith("ERASE "):
                    self.erase(s[6:].strip())
                    pc += 1

                elif u.startswith("DEFM"):
                    arg = s[4:].strip()
                    n = int(self.value(arg)) if arg else self.defm_count
                    if not 0 <= n <= 940:
                        raise RuntimeError("DEFM out of range")
                    self.arrays.clear()
                    self.defm_count = n
                    self.defm = [0.0] * (26 + n)
                    pc += 1

                elif u.startswith("READ "):
                    self.read_data(s[5:].strip())
                    pc += 1

                elif u == "RESTORE":
                    self.restore()
                    pc += 1

                elif u.startswith("RESTORE "):
                    self.restore(s[8:].strip())
                    pc += 1

                elif u.startswith("PRINT"):
                    self.do_print(s[5:].strip())
                    pc += 1

                elif u.startswith("INPUT "):
                    step_count = 0
                    self.do_input(s[6:].strip())
                    pc += 1

                elif u.startswith("LET "):
                    self.assign(s[4:].strip())
                    pc += 1

                elif re.match(r"^(?:[A-Z]\$?|\$)(?:\s*\([^)]*\))?\s*=", u):
                    self.assign(s)
                    pc += 1

                elif u.startswith("GOTO "):
                    pc = self.jump(self.value(s[5:]))

                elif u.startswith("GOSUB "):
                    self.gosub_stack.append(pc + 1)
                    pc = self.jump(self.value(s[6:]))

                elif u == "RETURN":
                    if not self.gosub_stack:
                        raise RuntimeError("RETURN without GOSUB")
                    pc = self.gosub_stack.pop()

                elif u.startswith("ON "):
                    m = re.match(r"^ON\s+(.+?)\s+GO(TO|SUB)\s+(.+)$", s, re.I)
                    if not m:
                        raise RuntimeError(f"bad ON statement: {s}")
                    expr, how, targets = m.groups()
                    n = int(self.value(expr))
                    lines = [int(self.value(x)) for x, _ in split_outside(targets, ",")]
                    if 1 <= n <= len(lines):
                        if how.upper() == "SUB":
                            self.gosub_stack.append(pc + 1)
                        pc = self.jump(lines[n - 1])
                    else:
                        pc += 1

                elif u.startswith("IF "):
                    m = re.match(r"^IF\s+(.+?)\s+THEN\s+(\d+)\s*$", s, re.I)
                    if not m:
                        raise RuntimeError("this interpreter uses IF ... THEN <line>")
                    cond, target = m.groups()
                    pc = self.jump(int(target)) if self.condition(cond) else pc + 1

                elif u.startswith("FOR "):
                    m = re.match(
                        r"^FOR\s+([A-Z])\s*=\s*(.+?)\s+TO\s+(.+?)(?:\s+STEP\s+(.+))?$",
                        s, re.I
                    )
                    if not m:
                        raise RuntimeError(f"bad FOR: {s}")
                    var, first, last, step = m.groups()
                    var = var.upper()
                    self.num[var] = float(self.value(first))
                    self.for_stack.append({
                        "var": var,
                        "limit": float(self.value(last)),
                        "step": float(self.value(step)) if step else 1.0,
                        "body": pc + 1,
                    })
                    pc += 1

                elif u.startswith("NEXT"):
                    if not self.for_stack:
                        raise RuntimeError("NEXT without FOR")
                    m = re.match(r"^NEXT(?:\s+([A-Z]))?\s*$", s, re.I)
                    if not m:
                        raise RuntimeError(f"bad NEXT: {s}")
                    loop = self.for_stack[-1]
                    if m.group(1) and m.group(1).upper() != loop["var"]:
                        raise RuntimeError("NEXT variable does not match FOR")
                    v = loop["var"]
                    self.num[v] += loop["step"]
                    more = self.num[v] <= loop["limit"] if loop["step"] >= 0 else self.num[v] >= loop["limit"]
                    if more:
                        pc = loop["body"]
                    else:
                        self.for_stack.pop()
                        pc += 1

                elif u.startswith("BEEP"):
                    # Terminal bell; ignore count/frequency details.
                    print("\a", end="", flush=True)
                    pc += 1

                else:
                    raise RuntimeError(f"unsupported statement: {s}")

            except Exception as e:
                raise RuntimeError(f"Error at BASIC line {line}: {e}") from e


def main():
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} program.bas")
        raise SystemExit(2)

    with open(sys.argv[1], encoding="utf-8") as f:
        source = f.read()

    vm = FX730P()
    vm.load(source)
    vm.run()
    print("(Program exited)")


if __name__ == "__main__":
    main()
