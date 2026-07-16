from __future__ import annotations

import re

from django import forms
from django.contrib.auth import get_user_model

from .models import Annotation
from .personalization import effective_response

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,29}$")
PIN_RE = re.compile(r"^\d{6}$")
RESET_CONFIRMATION = "REINITIALISER"


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


def pin_field(label: str, *, autocomplete: str) -> forms.CharField:
    return forms.CharField(
        label=label,
        min_length=6,
        max_length=6,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": autocomplete,
                "inputmode": "numeric",
                "pattern": "[0-9]{6}",
            }
        ),
    )


class PinConfirmationMixin:
    pin_field_name = "new_pin"
    confirmation_field_name = "new_pin_confirm"

    def clean(self):
        cleaned = super().clean()
        pin = cleaned.get(self.pin_field_name)
        confirmation = cleaned.get(self.confirmation_field_name)
        if pin and confirmation and pin != confirmation:
            self.add_error(
                self.confirmation_field_name,
                "Les deux codes PIN ne correspondent pas.",
            )
        return cleaned


class ChangePinForm(PinConfirmationMixin, forms.Form):
    current_pin = pin_field("Code PIN actuel", autocomplete="current-password")
    new_pin = pin_field("Nouveau code PIN", autocomplete="new-password")
    new_pin_confirm = pin_field(
        "Confirmer le nouveau code PIN",
        autocomplete="new-password",
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_pin(self):
        pin = self.cleaned_data["current_pin"]
        if not self.user.check_password(pin):
            raise forms.ValidationError("Le code PIN actuel est incorrect.")
        return pin

    def clean_new_pin(self):
        pin = self.cleaned_data["new_pin"]
        if not PIN_RE.fullmatch(pin):
            raise forms.ValidationError(
                "Le code PIN doit contenir exactement 6 chiffres."
            )
        return pin

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("current_pin")
            and cleaned.get("new_pin") == cleaned.get("current_pin")
        ):
            self.add_error(
                "new_pin",
                "Choisissez un code PIN différent du code actuel.",
            )
        return cleaned


class RecoveryForm(PinConfirmationMixin, forms.Form):
    username = UsernamePinForm.base_fields["username"]
    recovery_code = forms.CharField(
        label="Code de récupération",
        min_length=12,
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "autocapitalize": "characters",
                "spellcheck": "false",
                "placeholder": "XXXX-XXXX-XXXX",
            }
        ),
    )
    new_pin = pin_field("Nouveau code PIN", autocomplete="new-password")
    new_pin_confirm = pin_field(
        "Confirmer le nouveau code PIN",
        autocomplete="new-password",
    )

    def clean_username(self):
        username = normalize_username(self.cleaned_data["username"])
        if not USERNAME_RE.fullmatch(username):
            raise forms.ValidationError(
                "Utilisez un nom d'utilisateur valide."
            )
        return username

    def clean_new_pin(self):
        pin = self.cleaned_data["new_pin"]
        if not PIN_RE.fullmatch(pin):
            raise forms.ValidationError(
                "Le code PIN doit contenir exactement 6 chiffres."
            )
        return pin


class CurrentPinForm(forms.Form):
    current_pin = pin_field("Code PIN actuel", autocomplete="current-password")

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_pin(self):
        pin = self.cleaned_data["current_pin"]
        if not self.user.check_password(pin):
            raise forms.ValidationError("Le code PIN actuel est incorrect.")
        return pin


class ResetProgressForm(CurrentPinForm):
    confirmation = forms.CharField(
        label=f'Tapez « {RESET_CONFIRMATION} »',
        max_length=30,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "autocapitalize": "characters",
                "spellcheck": "false",
            }
        ),
    )

    def clean_confirmation(self):
        value = self.cleaned_data["confirmation"].strip().upper()
        if value != RESET_CONFIRMATION:
            raise forms.ValidationError(
                f'Tapez exactement « {RESET_CONFIRMATION} ».'
            )
        return value


class DeleteAccountForm(CurrentPinForm):
    username_confirmation = forms.CharField(
        label="Votre nom d'utilisateur",
        max_length=30,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "autocapitalize": "none",
                "spellcheck": "false",
            }
        ),
    )

    def clean_username_confirmation(self):
        value = normalize_username(
            self.cleaned_data["username_confirmation"]
        )
        if value != normalize_username(self.user.get_username()):
            raise forms.ValidationError(
                "Le nom d'utilisateur ne correspond pas."
            )
        return value


class PersonalResponseForm(forms.Form):
    reformulation = forms.CharField(
        label="Reformulation",
        required=False,
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    position = forms.CharField(
        label="Position",
        required=False,
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    position_claire = forms.CharField(
        label="Position claire",
        required=False,
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    nuance = forms.CharField(
        label="Nuance",
        required=False,
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    conclusion = forms.CharField(
        label="Conclusion",
        required=False,
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    argument_parts = (
        ("idea", "Idée principale", 3),
        ("developpement", "Développement", 4),
        ("exemple", "Exemple concret", 4),
        ("consequence", "Conséquence", 3),
    )

    def __init__(self, response, user, *args, **kwargs):
        self.response = response
        self.user = user
        super().__init__(*args, **kwargs)
        content = effective_response(response, user)
        self.argument_orders = []
        effective_arguments = {
            argument.order: argument for argument in content.arguments
        }
        for shared_argument in response.arguments.all():
            order = shared_argument.order
            self.argument_orders.append(order)
            argument = effective_arguments.get(order)
            for key, label, rows in self.argument_parts:
                field_name = f"argument_{order}_{key}"
                self.fields[field_name] = forms.CharField(
                    label=label,
                    required=False,
                    max_length=5000,
                    initial=(
                        getattr(argument, key)
                        if argument is not None
                        else getattr(shared_argument, key)
                    ),
                    widget=forms.Textarea(attrs={"rows": rows}),
                )
        if not self.is_bound:
            for field_name in (
                "reformulation",
                "position",
                "position_claire",
                "nuance",
                "conclusion",
            ):
                self.fields[field_name].initial = getattr(content, field_name)

    def personal_defaults(self):
        if not self.is_valid():
            raise ValueError("Cannot save an invalid personal response form.")
        arguments = []
        for order in self.argument_orders:
            argument = {"order": order}
            for key, _label, _rows in self.argument_parts:
                argument[key] = self.cleaned_data[
                    f"argument_{order}_{key}"
                ]
            arguments.append(argument)
        return {
            "reformulation": self.cleaned_data["reformulation"],
            "position": self.cleaned_data["position"],
            "position_claire": self.cleaned_data["position_claire"],
            "arguments": arguments,
            "nuance": self.cleaned_data["nuance"],
            "conclusion": self.cleaned_data["conclusion"],
        }


class NoteForm(forms.ModelForm):
    class Meta:
        model = Annotation
        fields = ("title", "body")
        labels = {
            "title": "Titre (facultatif)",
            "body": "Votre note",
        }
        widgets = {
            "title": forms.TextInput(
                attrs={"placeholder": "Ex. Connecteurs pour nuancer"}
            ),
            "body": forms.Textarea(
                attrs={
                    "rows": 5,
                    "placeholder": "Écrivez ce que vous voulez retenir…",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["body"].required = False

    def clean(self):
        cleaned = super().clean()
        body = (cleaned.get("body") or "").strip()
        if not body and not self.instance.quote.strip():
            self.add_error("body", "Écrivez une note avant de l'enregistrer.")
        return cleaned
