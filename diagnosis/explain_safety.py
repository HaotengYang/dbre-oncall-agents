"""Decides whether a captured statement may be given to EXPLAIN.

Captured SQL is untrusted: it was written by whoever sent the query. This is
an allowlist. One narrow shape passes, a single plain SELECT that calls only
known harmless functions, and every doubt is resolved by refusing. A refused
statement is reported as "not explained" with the reason; it is never
rewritten to make it pass.

Why so narrow. Measured on the lab's MySQL 8.0.40 (2026-11-21), plain EXPLAIN
is not free of side effects:

- A subquery in FROM is executed: EXPLAIN SELECT * FROM (SELECT SLEEP(3) AS x)
  AS derived took 3.0 s. So did a VALUES derived table and a LATERAL one.
- A function in WHERE can be executed: EXPLAIN SELECT id FROM orders WHERE
  id = 1 AND SLEEP(3) = 0 took 3.0 s. When the optimizer can reduce a table to
  one row it evaluates the remaining conditions on that row. (EXPLAIN SELECT
  id FROM orders WHERE id = SLEEP(3) returned at once, which is why one
  example is not a rule.)
- A backtick-quoted function name is still a function call: `SLEEP`(3) works.
- EXPLAIN ANALYZE executes the whole statement. It is never used, and a
  captured statement that is itself an EXPLAIN is refused.

Hence: functions are an allowlist, not a denylist; no subquery of any kind
(the words that start one are refused anywhere in the text); no backticks,
double quotes or dollar signs; ASCII only; and, as an extra layer, no
parenthesis in what the check takes to be the FROM clause.

Ordinary application SQL that is refused as a result, by design: anything with
backticks (most ORM output), JOIN ... ON (...) and USING (...), index hints,
PARTITION (...), inline OVER (...), CAST, CONVERT, EXTRACT, JSON functions,
GROUP_CONCAT, EXISTS, any subquery, CTE or UNION, and literals containing "@",
"#", "--", "$", double quotes, non-ASCII text or words such as "set", "table"
or "update". Such statements are reported as "not explained".

What the text check cannot see: a view whose definition does something slow,
and a statement that is merely expensive to plan. The timeout covers both.

The check looks at the raw text and never reasons "this part is inside a
string literal, so it is harmless". A harmless statement that mentions a
keyword in a string is refused too. That is the intended direction of error.

This is the first line of defence, not the only one. The account that runs
EXPLAIN has no write, EXECUTE or FILE privilege; its session is read-only; the
connection allows one statement per call; and the call has a timeout.
"""

import re

# Kept under the server's performance_schema_max_sql_text_length (4096 bytes in
# the lab): text at that length was probably cut off. The statement must be
# ASCII, so characters and bytes are the same count.
MAX_STATEMENT_LENGTH = 4000

# Functions that may appear. All are deterministic or read only the clock,
# never wait, lock, read files or change anything.
ALLOWED_FUNCTIONS = frozenset({
    "COUNT", "SUM", "MIN", "MAX", "AVG",
    "COALESCE", "IFNULL", "NULLIF", "IF", "GREATEST", "LEAST",
    "LOWER", "UPPER", "LENGTH", "CHAR_LENGTH", "CONCAT", "SUBSTRING", "SUBSTR",
    "TRIM", "LTRIM", "RTRIM", "LEFT", "RIGHT",
    "ABS", "ROUND", "FLOOR", "CEIL", "CEILING", "MOD",
    "DATE", "YEAR", "MONTH", "DAY", "HOUR", "DATE_FORMAT", "DATEDIFF", "TIMESTAMPDIFF",
    "DATE_ADD", "DATE_SUB", "NOW", "CURDATE", "UNIX_TIMESTAMP", "FROM_UNIXTIME",
})
# Words that are not functions and may be followed by "(".
WORDS_BEFORE_PARENTHESIS = frozenset({
    "IN", "AND", "OR", "NOT", "ON", "WHERE", "HAVING", "WHEN", "THEN", "ELSE", "BY",
    "SELECT", "DISTINCT", "BETWEEN", "LIKE", "IS", "AS",
})

# Keyword patterns use a loose left boundary: only a letter or underscore
# before the keyword makes it part of a longer word. A digit does not, because
# MySQL reads ".1UNION" as a number followed by the keyword UNION.
def _keyword(pattern):
    return re.compile(r"(?<![A-Za-z_])(?:" + pattern + r")(?![A-Za-z0-9_])", re.IGNORECASE)


_SELECT_KEYWORD = _keyword("SELECT")
_FORBIDDEN_KEYWORDS = (
    (_keyword(r"UNION|INTERSECT|EXCEPT"), "contains a set operation such as UNION"),
    # A second query block with no SELECT keyword of its own.
    (_keyword(r"VALUES|TABLE|LATERAL|JSON_TABLE"), "contains a subquery or table constructor"),
    (_keyword(r"INTO"), "contains INTO"),
    (_keyword(r"FOR\s+(?:UPDATE|SHARE)|LOCK\s+IN\s+SHARE\s+MODE"), "contains a locking clause"),
    (_keyword(
        r"UPDATE|DELETE|INSERT|DROP|ALTER|CREATE|TRUNCATE|RENAME|GRANT|REVOKE|CALL|"
        r"HANDLER|LOAD|KILL|FLUSH|SHUTDOWN|PREPARE|EXECUTE|ANALYZE|EXPLAIN|DESCRIBE|"
        r"PROCEDURE|DO|SET"
    ), "contains a keyword that does not belong in a plain SELECT"),
)
# What may stand directly before "(" when no word does: an operator, a comma,
# or another opening parenthesis.
_PUNCTUATION_BEFORE_PARENTHESIS = frozenset("=<>!+-*/%,(")
_IDENTIFIER_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_$."
)
_PLAIN_WORD = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")
_FROM_CLAUSE = re.compile(
    r"(?<![A-Za-z_])FROM(?![A-Za-z0-9_])(.*?)"
    r"(?=(?<![A-Za-z_])(?:WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|WINDOW)(?![A-Za-z0-9_])|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _parenthesis_refusal(statement):
    """Checks what stands before every "(".

    The whole run of identifier characters before the parenthesis is taken,
    including "$", "." and digits, so a name such as my$IF, 9IF or shop.IF is
    seen whole and not mistaken for the allowed word at its end. The reason
    returned never quotes the name: it is chosen by whoever wrote the query.
    """
    for position, character in enumerate(statement):
        if character != "(":
            continue
        before = statement[:position].rstrip()
        token_start = len(before)
        while token_start > 0 and before[token_start - 1] in _IDENTIFIER_CHARACTERS:
            token_start -= 1
        token = before[token_start:]
        if not token:
            if before and before[-1] not in _PUNCTUATION_BEFORE_PARENTHESIS:
                return "has a parenthesis after something that is not a plain function name"
            continue
        if not _PLAIN_WORD.match(token):
            return "calls a function whose name is qualified or not a plain word"
        if token.upper() not in ALLOWED_FUNCTIONS and token.upper() not in WORDS_BEFORE_PARENTHESIS:
            return "calls a function that is not on the list of allowed functions"
    return None


def explain_refusal(statement_text, truncated=False):
    """Returns why the statement must not be explained, or None if it may be."""
    if statement_text is None or not statement_text.strip():
        return "no statement text"
    if truncated:
        return "statement text was truncated by the server"
    if len(statement_text) >= MAX_STATEMENT_LENGTH:
        return "statement is too long and may have been truncated"
    if not statement_text.isascii() or any(
        ord(character) < 32 and character not in "\t\n\r" for character in statement_text
    ):
        return "contains characters outside plain ASCII"

    statement = statement_text.strip()
    if not re.match(r"SELECT(?![A-Za-z0-9_])", statement, re.IGNORECASE):
        return "not a SELECT statement"
    if ";" in statement.rstrip(";") or statement.endswith(";;"):
        return "more than one statement"
    if "/*" in statement or "*/" in statement or "--" in statement or "#" in statement:
        return "contains a comment"
    if "`" in statement or '"' in statement:
        # Under ANSI_QUOTES a double-quoted word is an identifier too.
        return "contains a backtick or double-quoted name"
    if "$" in statement:
        return "contains a dollar sign"
    if "@" in statement or ":=" in statement:
        return "contains a variable"
    if len(_SELECT_KEYWORD.findall(statement)) != 1:
        return "contains a subquery"
    for forbidden_pattern, reason in _FORBIDDEN_KEYWORDS:
        if forbidden_pattern.search(statement):
            return reason

    # Every FROM in the text is checked, not just the first: a string literal
    # may contain the word. This is defence in depth only: a string literal can
    # also end the FROM clause early as this regex sees it. What actually keeps
    # derived tables out is that SELECT, VALUES, TABLE, LATERAL and JSON_TABLE
    # are refused anywhere in the text.
    for from_clause in _FROM_CLAUSE.finditer(statement):
        if "(" in from_clause.group(1) or ")" in from_clause.group(1):
            return "has a parenthesis in the FROM clause"
    return _parenthesis_refusal(statement)
