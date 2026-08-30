# Copyright 2026 Tod Detre
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for LastPass items."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class LpassItem(BaseModel):
    """Immutable representation of a LastPass login item.

    Constructed from a successful ``lpass show`` result.  Fields that are
    absent in the LastPass entry default to empty strings.

    The secret-bearing fields (``password``, ``notes``) are
    :class:`~pydantic.types.SecretStr`, so they render as ``**********`` in
    ``repr()``, ``str()``, ``model_dump()``, ``model_dump_json()``, and any
    structlog call that binds the item.  Call ``.get_secret_value()`` to read
    the plaintext — deliberately explicit, so every place a secret escapes the
    model is greppable.

    Example::

        item = LpassItem(name="Homelab/My Secret", username="admin", password="s3cr3t")
        print(item.name)                        # "Homelab/My Secret"
        print(item.password)                    # "**********"
        print(item.password.get_secret_value())  # "s3cr3t"
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1, description="Item path/title in LastPass.")
    item_id: str = Field(default="", description="Numeric LastPass item ID, empty if unknown.")
    username: str = Field(default="", description="Username field value.")
    password: SecretStr = Field(default=SecretStr(""), description="Password field value (redacted in repr).")
    url: str = Field(default="", description="URL field value.")
    notes: SecretStr = Field(default=SecretStr(""), description="Notes field value (redacted in repr).")

    def with_password(self, password: str | SecretStr) -> "LpassItem":
        """Return a copy of this item with the password replaced.

        A plain ``str`` is wrapped in :class:`~pydantic.types.SecretStr` here
        rather than left to pydantic: ``model_copy(update=...)`` does **not**
        re-validate, so an unwrapped string would sit in the field and break
        ``.get_secret_value()`` at the point of use.

        Args:
            password: The new password value, as ``str`` or ``SecretStr``.

        Returns:
            A new LpassItem with the updated password and all other fields unchanged.
        """
        value = password if isinstance(password, SecretStr) else SecretStr(password)
        return self.model_copy(update={"password": value})

    def with_username(self, username: str) -> "LpassItem":
        """Return a copy of this item with the username replaced.

        Args:
            username: The new username value.

        Returns:
            A new LpassItem with the updated username and all other fields unchanged.
        """
        return self.model_copy(update={"username": username})
