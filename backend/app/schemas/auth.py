import re

from pydantic import BaseModel, Field, SecretStr, field_validator


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("password")
    @classmethod
    def validate_password_requirements(cls, value: str) -> str:
        if (
            len(value) < 8
            or not re.search(r"[A-Z]", value)
            or not re.search(r"[a-z]", value)
            or not re.search(r"[0-9]", value)
            or not re.search(r"[^A-Za-z0-9\s]", value)
        ):
            raise ValueError("Password does not meet the required format.")
        return value


class RegisterResponse(BaseModel):
    registered: bool
    authenticated: bool
    confirmation_required: bool
    message: str


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address.")
        return value


class LoginResponse(BaseModel):
    authenticated: bool
    message: str


class AuthenticatedUser(BaseModel):
    id: str
    email: str


class AuthMessageResponse(BaseModel):
    authenticated: bool
    message: str