"""Name binding belongs to the immutable model revision."""
from dataclasses import dataclass
from fractions import Fraction
from exact import q
from errors import InvalidModel, InvalidTask, UnknownVariable


def validate_names(names):
    names = tuple(names)
    if not 1 <= len(names) <= 8:
        raise InvalidModel("models require 1..8 variables")
    if any(not isinstance(s, str) or not s or s.strip() != s for s in names):
        raise InvalidModel("variable names must be nonempty trimmed strings")
    if len(set(names)) != len(names):
        raise InvalidModel("duplicate variable")
    return names


def identifier(value, kind):
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(kind + " must be a nonempty trimmed string")
    return value


@dataclass(frozen=True)
class Linear:
    name: str
    terms: tuple
    offset: Fraction = Fraction(0)

    @classmethod
    def make(cls, name, terms, offset=0):
        identifier(name, "expression name")
        items = terms.items() if hasattr(terms, "items") else terms
        seen = set()
        cleaned = []
        for variable, coefficient in items:
            identifier(variable, "variable")
            if variable in seen:
                raise InvalidTask("duplicate term")
            seen.add(variable)
            coefficient = q(coefficient)
            if coefficient:
                cleaned.append((variable, coefficient))
        return cls(name, tuple(sorted(cleaned)), q(offset))

    def bind(self, names):
        # Validate even when constructed directly rather than through make().
        canonical = Linear.make(self.name, self.terms, self.offset)
        unknown = {key for key, _ in canonical.terms} - set(names)
        if unknown:
            raise UnknownVariable(sorted(unknown)[0])
        terms = dict(canonical.terms)
        return tuple(terms.get(name, Fraction(0)) for name in names)

    def payload(self):
        return {"name": self.name, "terms": [[k, str(v)] for k, v in self.terms],
                "offset": str(self.offset)}

    @classmethod
    def from_payload(cls, obj):
        return cls.make(obj["name"], obj["terms"], obj["offset"])


@dataclass(frozen=True)
class Observation:
    expression: Linear
    value: Fraction

    @classmethod
    def make(cls, name, terms, value, offset=0):
        return cls(Linear.make(name, terms, offset), q(value))

    def payload(self):
        return {"expression": self.expression.payload(), "value": str(q(self.value))}

    @classmethod
    def from_payload(cls, obj):
        return cls(Linear.from_payload(obj["expression"]), q(obj["value"]))
