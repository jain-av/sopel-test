"""Types for creating section definitions.

A section definition consists of a subclass of :class:`StaticSection`, on which
any number of subclasses of :class:`BaseValidated` (a few common ones of which
are available in this module) are assigned as attributes. These descriptors
define how to read values from, and write values to, the config file.

As an example, if one wanted to define the ``[spam]`` section as having an
``eggs`` option, which contains a list of values, they could do this:

    >>> class SpamSection(StaticSection):
    ...     eggs = ListAttribute('eggs')
    ...
    >>> SpamSection(config, 'spam')
    >>> print(config.spam.eggs)
    []
    >>> config.spam.eggs = ['goose', 'turkey', 'duck', 'chicken', 'quail']
    >>> print(config.spam.eggs)
    ['goose', 'turkey', 'duck', 'chicken', 'quail']
    >>> config.spam.eggs = 'herring'
    Traceback (most recent call last):
        ...
    ValueError: ListAttribute value must be a list.

Configuration Inheritance and Descriptor Pattern
-------------------------------------------------

This module implements configuration sections using Python's descriptor protocol.
The inheritance mechanism works as follows:

1. **Section Definition**: Plugins define a subclass of :class:`StaticSection`
   with class-level :class:`BaseValidated` attributes that act as descriptors.

2. **Section Registration**: The section is registered with the config object
   via :meth:`~sopel.config.Config.define_section`, which instantiates the
   section class and attaches it to the config.

3. **Value Resolution**: When accessing an attribute (e.g., ``config.spam.eggs``),
   the descriptor's :meth:`~BaseValidated.__get__` method is invoked, which:

   a. Checks for environment variable overrides (``SOPEL_SECTION_ATTRIBUTE``)
   b. Falls back to the value from the config file
   c. Uses the attribute's default value if neither is set
   d. Raises :class:`AttributeError` if the value is required but missing

4. **Value Persistence**: When setting an attribute (e.g.,
   ``config.spam.eggs = [...]``), the descriptor's :meth:`~BaseValidated.__set__`
   method serializes the value and writes it to the underlying config parser.

This pattern provides type safety, validation, and a clean Python interface
while maintaining compatibility with INI-style config files. All section
classes inherit the same descriptor infrastructure from :class:`BaseValidated`,
ensuring consistent behavior across all configuration options.

.. seealso::

    :class:`~sopel.config.Config` for the main configuration class that manages
    sections, and :class:`~sopel.config.core_section.CoreSection` for the
    built-in ``[core]`` section that all bots require.
"""

from __future__ import annotations

import abc
import getpass
import os.path
import re

from sopel.tools import deprecated, get_input


class NO_DEFAULT:
    """A special value to indicate that there should be no default."""


class StaticSection:
    """A configuration section with parsed and validated settings.

    This class is intended to be subclassed and customized with added
    attributes containing :class:`BaseValidated`-based objects.

    .. note::

        By convention, subclasses of ``StaticSection`` are named with the
        plugin's name in CamelCase, plus the suffix ``Section``. For example, a
        plugin named ``editor`` might name its subclass ``EditorSection``; a
        ``do_stuff`` plugin might name its subclass ``DoStuffSection`` (its
        name converted from ``snake_case`` to ``CamelCase``).

        However, this is *only* a convention. Any class name that is legal in
        Python will work just fine.

    """
    def __init__(self, config, section_name, validate=True):
        """Initialize a StaticSection with validation.

        :param config: the parent configuration object
        :type config: :class:`~sopel.config.Config`
        :param str section_name: the name of this section in the config file
        :param bool validate: whether to validate attribute values during
                              initialization (optional; defaults to ``True``)
        :raise ValueError: if validation is enabled and any attribute has an
                           invalid or missing required value

        This method creates the section in the config parser if it doesn't
        exist, then iterates through all attributes defined on the class
        (which should be :class:`BaseValidated` descriptors) and attempts to
        access them to trigger validation. If validation fails, a descriptive
        error is raised indicating which setting is problematic.
        """
        if not config.parser.has_section(section_name):
            config.parser.add_section(section_name)
        # Store references to parent config and parser for descriptor access
        self._parent = config
        self._parser = config.parser
        self._section_name = section_name

        # Validation phase: iterate through all class attributes to trigger
        # descriptor __get__ methods, which will parse and validate values
        for value in dir(self):
            if value in ('_parent', '_parser', '_section_name'):
                # ignore internal attributes
                continue

            try:
                # Accessing the attribute triggers the descriptor's __get__,
                # which reads from config file and validates the value
                getattr(self, value)
            except ValueError as e:
                # Value exists but is invalid (wrong type, format, etc.)
                raise ValueError(
                    'Invalid value for {}.{}: {}'.format(
                        section_name, value, str(e)))
            except AttributeError:
                # Required value is missing (NO_DEFAULT with no value set)
                if validate:
                    raise ValueError(
                        'Missing required value for {}.{}'.format(
                            section_name, value))

    def configure_setting(self, name, prompt, default=NO_DEFAULT):
        """Return a validated value for this attribute from the terminal.

        :param str name: the name of the attribute to configure
        :param str prompt: the prompt text to display in the terminal
        :param default: the value to be used if the user does not enter one
        :type default: depends on subclass

        If ``default`` is passed, it will be used if no value is given by the
        user. If it is not passed, the current value of the setting, or the
        default value if it's unset, will be used. Note that if ``default`` is
        passed, the current value of the setting will be ignored, even if it is
        not the attribute's default.
        """
        attribute = getattr(self.__class__, name)
        if default is NO_DEFAULT and not attribute.is_secret:
            try:
                default = getattr(self, name)
            except AttributeError:
                pass
            except ValueError:
                print('The configured value for this option was invalid.')
                if attribute.default is not NO_DEFAULT:
                    default = attribute.default
        while True:
            try:
                value = attribute.configure(
                    prompt, default, self._parent, self._section_name)
            except ValueError as exc:
                print(exc)
            else:
                break
        setattr(self, name, value)


class BaseValidated(abc.ABC):
    """The base type for a setting descriptor in a :class:`StaticSection`.

    :param str name: the attribute name to use in the config file
    :param default: the value to be returned if the setting has no value
                    (optional; defaults to :obj:`None`)
    :type default: mixed
    :param bool is_secret: tell if the attribute is secret/a password
                           (optional; defaults to ``False``)

    ``default`` also can be set to :const:`sopel.config.types.NO_DEFAULT`, if
    the value *must* be configured by the user (i.e. there is no suitable
    default value). Trying to read an empty ``NO_DEFAULT`` value will raise
    :class:`AttributeError`.

    .. important::

        Setting names SHOULD follow *snake_case* naming rules:

        * use only lowercase letters, digits, and underscore (``_``)
        * SHOULD NOT start with a digit

        Deviations from *snake_case* can break the following operations:

        * :ref:`accessing the setting <sopel.config>` from Python code using
          the :class:`~.Config` object's attributes
        * :ref:`overriding the setting's value <Overriding individual
          settings>` using environment variables

    """
    def __init__(self, name, default=None, is_secret=False):
        self.name = name
        """Name of the attribute."""
        self.default = default
        """Default value for this attribute.

        If not specified, the attribute's default value will be ``None``.
        """
        self.is_secret = bool(is_secret)
        """Tell if the attribute is secret/a password.

        The default value is ``False`` (not secret).

        Sopel's configuration can contain passwords, secret keys, and other
        private information that must be treated as sensitive data. Such
        options should be marked as "secret" with this attribute.
        """

    def configure(self, prompt, default, parent, section_name):
        """Parse and return a value from user's input.

        :param str prompt: text to show the user
        :param mixed default: default value used if no input given
        :param parent: usually the parent Config object
        :type parent: :class:`~sopel.config.Config`
        :param str section_name: the name of the containing section

        This method displays the ``prompt`` and waits for user's input on the
        terminal. If no input is provided (i.e. the user just presses "Enter"),
        the ``default`` value is returned instead.

        If :attr:`.is_secret` is ``True``, the input will be hidden, using the
        built-in :func:`~getpass.getpass` function.
        """
        if default is not NO_DEFAULT and default is not None:
            prompt = '{} [{}]'.format(prompt, default)

        if self.is_secret:
            value = getpass.getpass(prompt + ' (hidden input) ')
        else:
            value = get_input(prompt + ' ')

        if not value and default is NO_DEFAULT:
            raise ValueError("You must provide a value for this option.")

        value = value or default
        section = getattr(parent, section_name)

        return self._parse(value, parent, section)

    @abc.abstractmethod
    def serialize(self, value, *args, **kwargs):
        """Take some object, and return the string to be saved to the file."""

    @abc.abstractmethod
    def parse(self, value, *args, **kwargs):
        """Take a string from the file, and return the appropriate object."""

    def __get__(self, instance, owner=None):
        """Descriptor protocol: retrieve and parse the attribute value.

        :param instance: the :class:`StaticSection` instance, or ``None`` if
                         accessed from the class
        :type instance: :class:`StaticSection` or ``None``
        :param owner: the :class:`StaticSection` class itself (optional)
        :type owner: class
        :return: the parsed configuration value, or the descriptor itself if
                 accessed from the class
        :raise AttributeError: if the value is required but not set

        This method implements the descriptor protocol's getter. It follows a
        value resolution order: environment variables take precedence over
        config file values, which take precedence over defaults.
        """
        if instance is None:
            # If instance is None, we're getting from a section class, not an
            # instance of a section class. It makes the wizard code simpler
            # (and is really just more intuitive) to return the descriptor
            # instance here.
            return self

        # Value resolution order: environment variable > config file > default
        value = None
        # Check for environment variable override (e.g., SOPEL_CORE_NICK)
        env_name = 'SOPEL_%s_%s' % (instance._section_name.upper(), self.name.upper())
        if env_name in os.environ:
            value = os.environ.get(env_name)
        # Otherwise, read from config file if present
        elif instance._parser.has_option(instance._section_name, self.name):
            value = instance._parser.get(instance._section_name, self.name)

        # Parse the raw string value (or use default if value is None)
        settings = instance._parent
        section = getattr(settings, instance._section_name)
        return self._parse(value, settings, section)

    def _parse(self, value, settings, section):
        """Internal parse helper that handles default values.

        :param value: the raw value from config file or environment variable,
                      or ``None`` if not set
        :type value: str or ``None``
        :param settings: the parent config object
        :type settings: :class:`~sopel.config.Config`
        :param section: the section instance containing this attribute
        :type section: :class:`StaticSection`
        :return: the parsed value
        :raise AttributeError: if value is ``None`` and no default is set

        This helper method is called by :meth:`__get__` to handle the parsing
        logic with fallback to default values for optional settings.
        """
        if value is not None:
            # Value exists, parse it using subclass-specific parse() method
            return self.parse(value)
        if self.default is not NO_DEFAULT:
            # No value set, use the default if available
            return self.default
        # No value and no default: this is a required setting that's missing
        raise AttributeError(
            "Missing required value for {}.{}".format(
                section._section_name, self.name
            )
        )

    def __set__(self, instance, value):
        """Descriptor protocol: set the attribute value in the config file.

        :param instance: the :class:`StaticSection` instance
        :type instance: :class:`StaticSection`
        :param value: the value to set, or ``None`` to remove the option
        :raise ValueError: if trying to set ``None`` on a required option

        Setting a value serializes it to a string and writes it to the
        underlying config parser. Setting ``None`` removes the option from
        the config file (unless it's required with NO_DEFAULT).
        """
        if value is None:
            # Attempting to unset the value
            if self.default == NO_DEFAULT:
                raise ValueError('Cannot unset an option with a required value.')
            # Remove the option from config file
            instance._parser.remove_option(instance._section_name, self.name)
            return

        # Serialize and write the value to config file
        settings = instance._parent
        section = getattr(settings, instance._section_name)
        value = self._serialize(value, settings, section)
        instance._parser.set(instance._section_name, self.name, value)

    def _serialize(self, value, settings, section):
        """Internal serialize helper for value conversion.

        :param value: the value to serialize
        :param settings: the parent config object
        :type settings: :class:`~sopel.config.Config`
        :param section: the section instance containing this attribute
        :type section: :class:`StaticSection`
        :return: the serialized string representation
        :rtype: str

        This helper method delegates to the subclass-specific :meth:`serialize`
        method. It's provided as an extension point for subclasses that need
        access to the config context during serialization.
        """
        return self.serialize(value)

    def __delete__(self, instance):
        """Descriptor protocol: delete the attribute from the config file.

        :param instance: the :class:`StaticSection` instance
        :type instance: :class:`StaticSection`

        This removes the option from the underlying config parser entirely.
        """
        instance._parser.remove_option(instance._section_name, self.name)


def _parse_boolean(value):
    """Parse various representations of boolean values.

    :param value: the value to parse as boolean
    :type value: bool, int, str, or mixed
    :return: ``True`` or ``False``
    :rtype: bool

    This helper function recognizes multiple string representations:
    - Truthy strings: '1', 'yes', 'y', 'true', 'on' (case-insensitive)
    - Literal: ``True`` or ``1`` (integer)
    - All other values: converted via ``bool()``
    """
    if value is True or value == 1:
        return value
    if isinstance(value, str):
        return value.lower() in ['1', 'yes', 'y', 'true', 'on']
    return bool(value)


def _serialize_boolean(value):
    """Serialize a boolean value to a config file string.

    :param value: the boolean value to serialize
    :type value: bool or mixed
    :return: 'true' or 'false' (lowercase)
    :rtype: str

    This uses :func:`_parse_boolean` to normalize the input value first,
    ensuring consistent string output regardless of input format.
    """
    return 'true' if _parse_boolean(value) else 'false'


@deprecated(
    reason='Use BooleanAttribute instead of ValidatedAttribute with parse=bool',
    version='7.1',
    warning_in='8.0',
    removed_in='9.0',
    stack_frame=-2,
)
def _deprecated_special_bool_handling(serialize):
    if not serialize or serialize == bool:
        serialize = _serialize_boolean

    return _parse_boolean, serialize


class ValidatedAttribute(BaseValidated):
    """A descriptor for settings in a :class:`StaticSection`.

    :param str name: the attribute name to use in the config file
    :param parse: a function to be used to read the string and create the
                  appropriate object (optional; the string value will be
                  returned as-is if not set)
    :type parse: :term:`function`
    :param serialize: a function that, given an object, should return a string
                      that can be written to the config file safely (optional;
                      defaults to :class:`str`)
    :type serialize: :term:`function`
    :param bool is_secret: ``True`` when the attribute should be considered
                           a secret, like a password (default to ``False``)
    """
    def __init__(self,
                 name,
                 parse=None,
                 serialize=None,
                 default=None,
                 is_secret=False):
        super().__init__(name, default=default, is_secret=is_secret)

        if parse == bool:
            parse, serialize = _deprecated_special_bool_handling(serialize)

        self.parse = parse or self.parse
        self.serialize = serialize or self.serialize

    def serialize(self, value):
        """Return the ``value`` as a Unicode string.

        :param value: the option value
        :rtype: str
        """
        return str(value)

    def parse(self, value):
        """No-op: simply returns the given ``value``, unchanged.

        :param str value: the string read from the config file
        :rtype: str
        """
        return value

    def configure(self, prompt, default, parent, section_name):
        if self.parse == _parse_boolean:
            prompt += ' (y/n)'
            default = 'y' if default else 'n'
        return super().configure(prompt, default, parent, section_name)


class BooleanAttribute(BaseValidated):
    """A descriptor for Boolean settings in a :class:`StaticSection`.

    :param str name: the attribute name to use in the config file
    :param bool default: the default value to use if this setting is not
                         present in the config file

    If the ``default`` value is not specified, it will be ``False``.
    """
    def __init__(self, name, default=False):
        super().__init__(name, default=default, is_secret=False)

    def configure(self, prompt, default, parent, section_name):
        """Parse and return a value from user's input.

        :param str prompt: text to show the user
        :param bool default: default value used if no input given
        :param parent: usually the parent Config object
        :type parent: :class:`~sopel.config.Config`
        :param str section_name: the name of the containing section

        This method displays the ``prompt`` and waits for user's input on the
        terminal. If no input is provided (i.e. the user just presses "Enter"),
        the ``default`` value is returned instead.
        """
        prompt = '{} ({})'.format(prompt, 'Y/n' if default else 'y/N')
        value = get_input(prompt + ' ') or default
        section = getattr(parent, section_name)
        return self._parse(value, parent, section)

    def serialize(self, value):
        """Convert a Boolean value to a string for saving to the config file.

        :param bool value: the value to serialize
        """
        return 'true' if self.parse(value) else 'false'

    def parse(self, value):
        """Parse a limited set of values/objects into Boolean representations.

        :param mixed value: the value to parse

        The literal values ``True`` or ``1`` will be parsed as ``True``. The
        strings ``'1'``, ``'yes'``, ``'y'``, ``'true'``, ``'enable'``,
        ``'enabled'``, and ``'on'`` will also be parsed as ``True``,
        regardless of case. All other values will be parsed as ``False``.
        """
        if value is True or value == 1:
            return True
        if isinstance(value, str):
            return value.lower() in [
                '1',
                'enable',
                'enabled',
                'on',
                'true',
                'y',
                'yes',
            ]
        return bool(value)

    def __set__(self, instance, value):
        if value is None:
            instance._parser.remove_option(instance._section_name, self.name)
            return

        settings = instance._parent
        section = getattr(settings, instance._section_name)
        value = self._serialize(value, settings, section)
        instance._parser.set(instance._section_name, self.name, value)


class SecretAttribute(ValidatedAttribute):
    """A config attribute containing a value which must be kept secret.

    This attribute is always considered to be secret/sensitive data, but
    otherwise behaves like other any option.
    """
    def __init__(self, name, parse=None, serialize=None, default=None):
        super().__init__(
            name,
            parse=parse,
            serialize=serialize,
            default=default,
            is_secret=True,
        )


class ListAttribute(BaseValidated):
    """A config attribute containing a list of string values.

    :param str name: the attribute name to use in the config file
    :param strip: whether to strip whitespace from around each value
                  (optional; applies only to legacy comma-separated lists;
                  multi-line lists are always stripped)
    :type strip: bool
    :param default: the default value if the config file does not define a
                    value for this option; to require explicit configuration,
                    use :const:`sopel.config.types.NO_DEFAULT` (optional)
    :type default: list

    From this :class:`StaticSection`::

        class SpamSection(StaticSection):
            cheeses = ListAttribute('cheeses')

    the option will be exposed as a Python :class:`list`::

        >>> config.spam.cheeses
        ['camembert', 'cheddar', 'reblochon', '#brie']

    which comes from this configuration file:

    .. code-block:: ini

        [spam]
        cheeses =
            camembert
            cheddar
            reblochon
            "#brie"

    Note that the ``#brie`` item starts with a ``#``, hence the double quote:
    without these quotation marks, the config parser would think it's a
    comment. The quote/unquote is managed automatically by this field, and
    if and only if it's necessary (see :meth:`parse` and :meth:`serialize`).

    .. versionchanged:: 7.0

        The option's value will be split on newlines by default. In this
        case, the ``strip`` parameter has no effect.

        See the :meth:`parse` method for more information.

    .. note::

        **About:** backward compatibility with comma-separated values.

        A :class:`ListAttribute` option allows to write, on a single line,
        the values separated by commas. As of Sopel 7.x this behavior is
        discouraged. It will be deprecated in Sopel 8.x, then removed in
        Sopel 9.x.

        Bot owners are encouraged to update their configurations to use
        newlines instead of commas.

        The comma delimiter fallback does not support commas within items in
        the list.
    """
    DELIMITER = ','
    QUOTE_REGEX = re.compile(r'^"(?P<value>#.*)"$')
    """Regex pattern to match value that requires quotation marks.

    This pattern matches values that start with ``#`` inside quotation marks
    only: ``"#sopel"`` will match, but ``"sopel"`` won't, and neither will any
    variant that doesn't conform to this pattern.
    """

    def __init__(self, name, strip=True, default=None):
        default = default or []
        super().__init__(name, default=default)
        self.strip = strip

    def parse(self, value):
        """Parse ``value`` into a list.

        :param str value: a multi-line string of values to parse into a list
        :return: a list of items from ``value``
        :rtype: list

        .. versionchanged:: 7.0

            The value is now split on newlines, with fallback to comma
            when there is no newline in ``value``.

            When modified and saved to a file, items will be stored as a
            multi-line string (see :meth:`serialize`).

        The parsing logic prefers newline-separated values (the modern format)
        but falls back to comma-separated values for backward compatibility
        with older config files.
        """
        if "\n" in value:
            # Modern multi-line format: split on newlines
            items = (
                # remove trailing comma (for mixed format support)
                # because `value,\nother` is valid in Sopel 7.x
                item.strip(self.DELIMITER).strip()
                for item in value.splitlines())
        else:
            # Legacy comma-separated format for backward compatibility
            # this behavior will be:
            # - Discouraged in Sopel 7.x (in the documentation)
            # - Deprecated in Sopel 8.x
            # - Removed from Sopel 9.x
            items = value.split(self.DELIMITER)

        # Parse each item (handles unquoting of # prefixed values)
        items = (self.parse_item(item) for item in items if item)
        # Apply whitespace stripping if enabled
        if self.strip:
            return [item.strip() for item in items]

        return list(items)

    def parse_item(self, item):
        """Parse one ``item`` from the list.

        :param str item: one item from the list to parse
        :rtype: str

        If ``item`` matches the :attr:`QUOTE_REGEX` pattern, then it will be
        unquoted. Otherwise it's returned as-is.

        This handles the special case where values starting with ``#`` are
        quoted in the config file to prevent them from being parsed as comments.
        """
        # Check if item is quoted (e.g., "#channel" -> remove quotes)
        result = self.QUOTE_REGEX.match(item)
        if result:
            # Extract the value inside quotes (just the # prefixed content)
            return result.group('value')
        # No quotes needed/found, return as-is
        return item

    def serialize(self, value):
        """Serialize ``value`` into a multi-line string.

        :param list value: the input list
        :rtype: str
        :raise ValueError: if ``value`` is the wrong type (i.e. not a list)

        The serialized format always uses newlines, which is the modern
        standard format. This ensures that when the config is read again,
        the newline format will be detected and comma-separated parsing
        will be bypassed.
        """
        if not isinstance(value, (list, set)):
            raise ValueError('ListAttribute value must be a list.')
        elif not value:
            # return an empty string when there is no value
            return ''

        # Leading newline ensures multi-line format detection on re-read
        # This way, comma delimiter will be ignored when the configuration
        # file is parsed again later
        return '\n' + '\n'.join(self.serialize_item(item) for item in value)

    def serialize_item(self, item):
        """Serialize an ``item`` from the list value.

        :param str item: one item of the list to serialize
        :rtype: str

        If ``item`` starts with a ``#`` it will be quoted in order to prevent
        the config parser from thinking it's a comment.

        This quoting is transparent to users: the quotes are added during
        serialization and removed during parsing via :meth:`parse_item`.
        """
        if item.startswith('#'):
            # Protect items that would otherwise be parsed as comments
            # Example: #sopel -> "#sopel" in config file
            return '"%s"' % item
        # No special handling needed
        return item

    def configure(self, prompt, default, parent, section_name):
        each_prompt = '?'
        if isinstance(prompt, tuple):
            each_prompt = prompt[1]
            prompt = prompt[0]

        if default is not NO_DEFAULT:
            default_prompt = ','.join(['"{}"'.format(item) for item in default])
            prompt = '{} [{}]'.format(prompt, default_prompt)
        else:
            default = []
        print(prompt)
        values = []
        value = get_input(each_prompt + ' ') or default
        if (value == default) and not default:
            value = ''
        while value:
            if value == default:
                values.extend(value)
            else:
                values.append(value)
            value = get_input(each_prompt + ' ')

        section = getattr(parent, section_name)
        values = self._serialize(values, parent, section)
        return self._parse(values, parent, section)


class ChoiceAttribute(BaseValidated):
    """A config attribute which must be one of a set group of options.

    :param str name: the attribute name to use in the config file
    :param choices: acceptable values; currently, only strings are supported
    :type choices: list or tuple
    :param default: which choice to use if none is set in the config file; to
                    require explicit configuration, use
                    :const:`sopel.config.types.NO_DEFAULT` (optional)
    :type default: str

    This attribute type restricts values to a predefined set of choices,
    validating both on read and write operations. Any attempt to set a value
    not in the choices list will raise :class:`ValueError`.

    **Usage Example:**

    For a plugin that needs a difficulty setting::

        from sopel.config import types

        class GameSection(types.StaticSection):
            difficulty = types.ChoiceAttribute(
                'difficulty',
                choices=['easy', 'normal', 'hard', 'expert'],
                default='normal'
            )

    In the config file:

    .. code-block:: ini

        [game]
        difficulty = hard

    Accessing the value::

        >>> config.game.difficulty
        'hard'
        >>> config.game.difficulty = 'impossible'
        Traceback (most recent call last):
            ...
        ValueError: Value must be in ['easy', 'normal', 'hard', 'expert']

    .. seealso::

        :class:`ValidatedAttribute` for unrestricted string values, or
        :class:`BooleanAttribute` for binary choices.
    """
    def __init__(self, name, choices, default=None):
        super().__init__(name, default=default)
        self.choices = choices

    def parse(self, value):
        """Check the loaded ``value`` against the valid ``choices``.

        :param str value: the value loaded from the config file
        :return: the ``value``, if it is valid
        :rtype: str
        :raise ValueError: if ``value`` is not one of the valid ``choices``

        This validation ensures that only predefined choices can be loaded
        from the config file, preventing configuration errors at startup.
        """
        if value in self.choices:
            # Value is valid, return as-is
            return value
        else:
            # Value not in allowed choices, reject with clear error message
            raise ValueError('Value must be in {}'.format(self.choices))

    def serialize(self, value):
        """Make sure ``value`` is valid and safe to write in the config file.

        :param str value: the value needing to be saved
        :return: the ``value``, if it is valid
        :rtype: str
        :raise ValueError: if ``value`` is not one of the valid ``choices``

        This validation prevents invalid values from being programmatically
        set at runtime, ensuring config file integrity.
        """
        if value in self.choices:
            # Value is valid, return as-is for writing to config
            return value
        else:
            # Value not in allowed choices, reject before writing to file
            raise ValueError('Value must be in {}'.format(self.choices))


class FilenameAttribute(BaseValidated):
    """A config attribute which must be a file or directory.

    :param str name: the attribute name to use in the config file
    :param relative: whether the path should be relative to the location of
                     the config file (optional; note that absolute paths will
                     always be interpreted as absolute)
    :type relative: bool
    :param directory: whether the path should indicate a directory, rather
                      than a file (optional)
    :type directory: bool
    :param default: the value to use if none is defined in the config file; to
                    require explicit configuration, use
                    :const:`sopel.config.types.NO_DEFAULT` (optional)
    :type default: str
    """
    def __init__(self, name, relative=True, directory=False, default=None):
        super().__init__(name, default=default)
        self.relative = relative
        self.directory = directory

    def _parse(self, value, settings, section):
        if value is None:
            if self.default == NO_DEFAULT:
                raise AttributeError(
                    "Missing required value for {}.{}".format(
                        section._section_name, self.name
                    )
                )
            value = self.default

        if not value:
            return self.parse(value)

        result = os.path.expanduser(value)
        if not os.path.isabs(result):
            if not self.relative:
                raise ValueError("Value must be an absolute path.")
            result = os.path.join(settings.homedir, result)

        return self.parse(result)

    def _serialize(self, value, settings, section):
        """Used to validate ``value`` when it is changed at runtime.

        :param settings: the config object which contains this attribute
        :type settings: :class:`~sopel.config.Config`
        :param section: the config section which contains this attribute
        :type section: :class:`~StaticSection`
        :return: the ``value``, if it is valid
        :rtype: str
        :raise ValueError: if the ``value`` is not valid
        """
        self._parse(value, settings, section)
        return self.serialize(value)

    def parse(self, value):
        """Parse ``value`` as a path on the filesystem to check.

        :param str value: the path to check
        :rtype: str
        :raise ValueError: if the directory or file doesn't exist and cannot
                           be created

        If there is no ``value``, then this returns ``None``. Otherwise, it'll
        check if the directory or file exists. If it doesn't, it'll try to
        create it.
        """
        if not value:
            return None

        if self.directory and not os.path.isdir(value):
            try:
                os.makedirs(value)
            except (IOError, OSError):
                raise ValueError(
                    "Value must be an existing or creatable directory.")
        if not self.directory and not os.path.isfile(value):
            try:
                open(value, 'w').close()
            except (IOError, OSError):
                raise ValueError("Value must be an existing or creatable file.")
        return value

    def serialize(self, value):
        """Directly return the ``value`` without modification.

        :param str value: the value needing to be saved
        :return: the unaltered ``value``, if it is valid
        :rtype: str

        Managing the filename is done by other methods (:meth:`parse`), and
        this method is a no-op: this way it ensures that relative paths won't
        be replaced by absolute ones.
        """
        return value  # So that it's still relative
