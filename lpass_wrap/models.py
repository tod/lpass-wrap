# Copyright 2026 Tod Detre
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pydantic models for LastPass items."""

from pydantic import BaseModel, ConfigDict, Field


class LpassItem(BaseModel):
    """Immutable representation of a LastPass login item.

    Constructed from a successful ``lpass show`` result.  Fields that are
    absent in the LastPass entry default to empty strings.

    Example::

        item = LpassItem(name="Homelab/My Secret", username="admin", password="s3cr3t")
        print(item.name)      # "Homelab/My Secret"
        print(item.password)  # "s3cr3t"
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1, description="Item path/title in LastPass.")
    item_id: str = Field(default="", description="Numeric LastPass item ID, empty if unknown.")
    username: str = Field(default="", description="Username field value.")
    password: str = Field(default="", description="Password field value.")
    url: str = Field(default="", description="URL field value.")
    notes: str = Field(default="", description="Notes field value.")

    def with_password(self, password: str) -> "LpassItem":
        """Return a copy of this item with the password replaced.

        Args:
            password: The new password value.

        Returns:
            A new LpassItem with the updated password and all other fields unchanged.
        """
        return self.model_copy(update={"password": password})

    def with_username(self, username: str) -> "LpassItem":
        """Return a copy of this item with the username replaced.

        Args:
            username: The new username value.

        Returns:
            A new LpassItem with the updated username and all other fields unchanged.
        """
        return self.model_copy(update={"username": username})
