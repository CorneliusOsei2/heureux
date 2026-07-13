from __future__ import annotations

import re

from django import forms
from django.contrib.auth import get_user_model

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,29}$")
PIN_RE = re.compile(r"^\d{6}$")


def normalize_username(value: str) -> str:
    return value.strip().lower()


class UsernamePinForm(forms.Form):
    username = forms.CharField(
        label="Nom d'utilisateur",
        min_length=3,
        max_length=30,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "username",
                "autocapitalize": "none",
                "spellcheck": "false",
            }
        ),
    )
    pin = forms.CharField(
        label="Code PIN",
        min_length=6,
        max_length=6,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "inputmode": "numeric",
                "pattern": "[0-9]{6}",
            }
        ),
    )

    def clean_username(self):
        username = normalize_username(self.cleaned_data["username"])
        if not USERNAME_RE.fullmatch(username):
            raise forms.ValidationError(
                "Utilisez 3 à 30 caractères : lettres, chiffres, point, tiret ou soulignement."
            )
        return username

    def clean_pin(self):
        pin = self.cleaned_data["pin"]
        if not PIN_RE.fullmatch(pin):
            raise forms.ValidationError("Le code PIN doit contenir exactement 6 chiffres.")
        return pin


class RegistrationForm(UsernamePinForm):
    pin_confirm = forms.CharField(
        label="Confirmer le code PIN",
        min_length=6,
        max_length=6,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "inputmode": "numeric",
                "pattern": "[0-9]{6}",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pin"].widget.attrs["autocomplete"] = "new-password"

    def clean_username(self):
        username = super().clean_username()
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Ce nom d'utilisateur est déjà utilisé.")
        return username

    def clean(self):
        cleaned = super().clean()
        pin = cleaned.get("pin")
        confirmation = cleaned.get("pin_confirm")
        if pin and confirmation and pin != confirmation:
            self.add_error("pin_confirm", "Les deux codes PIN ne correspondent pas.")
        elif confirmation and not PIN_RE.fullmatch(confirmation):
            self.add_error(
                "pin_confirm",
                "Le code PIN doit contenir exactement 6 chiffres.",
            )
        return cleaned
