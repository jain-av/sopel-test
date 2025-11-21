#!/usr/bin/env python3
"""Test file for comment removal script."""


def example_function():
    """This is a docstring that should be preserved."""
    x = 5
    y = 10
    url = "https://example.com#anchor"
    result = x + y
    return result


def function_with_inline_comments():
    """Function with inline comments."""
    value = 42
    text = "This has a # inside a string"
    another = 'Also # in single quotes'
    return value, text, another


class ExampleClass:
    """Class docstring to preserve."""

    def __init__(self):
        """Constructor docstring."""
        self.value = 100

    def method(self):
        """Method docstring."""
        return self.value * 2
