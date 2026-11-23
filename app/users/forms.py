import re

from django import forms

from .models import IntegrationSettings, User, UserRole, RoleCode


class IntegrationSettingsForm(forms.ModelForm):
    openai_api_key = forms.CharField(
        label="OpenAI API Key",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input input-bordered w-full font-mono text-sm",
                "placeholder": "sk-…",
                "autocomplete": "off",
                "spellcheck": "false",
            }
        ),
        help_text="Clave secreta de OpenAI para leer facturas.",
    )
    gemini_api_key = forms.CharField(
        label="Gemini API Key",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input input-bordered w-full font-mono text-sm",
                "placeholder": "AIza…",
                "autocomplete": "off",
                "spellcheck": "false",
            }
        ),
        help_text="Clave de Google AI Studio / Gemini API.",
    )

    class Meta:
        model = IntegrationSettings
        fields = [
            "projects_api_url",
            "projects_api_key",
            "openai_enabled",
            "openai_model",
            "openai_api_key",
            "gemini_enabled",
            "gemini_model",
            "gemini_min_confidence",
            "gemini_api_key",
        ]
        widgets = {
            "projects_api_url": forms.URLInput(
                attrs={"class": "input input-bordered w-full"}
            ),
            "projects_api_key": forms.TextInput(
                attrs={"class": "input input-bordered w-full"}
            ),
            "openai_enabled": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "openai_model": forms.Select(
                attrs={"class": "select select-bordered w-full"}
            ),
            "gemini_enabled": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "gemini_model": forms.Select(
                attrs={"class": "select select-bordered w-full"}
            ),
            "gemini_min_confidence": forms.NumberInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "min": 0,
                    "max": 100,
                    "step": 1,
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["openai_model"].choices = IntegrationSettings.OpenAIModel.choices
        self.fields["gemini_model"].choices = IntegrationSettings.GeminiModel.choices
        # Si había un valor legacy (ej. gpt-4o), mostrarlo hasta que elijan otro.
        current = ""
        if self.instance and self.instance.pk:
            current = (self.instance.openai_model or "").strip()
        valid = {c[0] for c in IntegrationSettings.OpenAIModel.choices}
        if current and current not in valid:
            self.fields["openai_model"].choices = [
                (current, f"{current} (legado)"),
                *IntegrationSettings.OpenAIModel.choices,
            ]
        g_current = ""
        if self.instance and self.instance.pk:
            g_current = (self.instance.gemini_model or "").strip()
        g_valid = {c[0] for c in IntegrationSettings.GeminiModel.choices}
        if g_current and g_current not in g_valid:
            self.fields["gemini_model"].choices = [
                (g_current, f"{g_current} (legado)"),
                *IntegrationSettings.GeminiModel.choices,
            ]
        if self.instance and self.instance.pk and self.instance.openai_api_key:
            self.fields["openai_api_key"].help_text = (
                "Key guardada (visible). Puedes editarla o dejarla igual."
            )
        else:
            self.fields["openai_api_key"].help_text = (
                "Obligatoria si activas la lectura con IA. Consíguela en platform.openai.com."
            )
            self.fields["openai_api_key"].widget.attrs["placeholder"] = "sk-…"
        if self.instance and self.instance.pk and self.instance.gemini_api_key:
            self.fields["gemini_api_key"].help_text = (
                "Key guardada (visible). Puedes editarla o dejarla igual."
            )
        else:
            self.fields["gemini_api_key"].help_text = (
                "Obligatoria si activas la verificación visual. Consíguela en aistudio.google.com."
            )
            self.fields["gemini_api_key"].widget.attrs["placeholder"] = "AIza…"

        # Mostrar el valor guardado en el input (texto visible).
        if self.instance and self.instance.pk and not self.is_bound:
            if self.instance.openai_api_key:
                self.fields["openai_api_key"].initial = self.instance.openai_api_key
            if self.instance.gemini_api_key:
                self.fields["gemini_api_key"].initial = self.instance.gemini_api_key

    def clean_openai_api_key(self):
        return (self.cleaned_data.get("openai_api_key") or "").strip()

    def clean_gemini_api_key(self):
        return (self.cleaned_data.get("gemini_api_key") or "").strip()

    def clean(self):
        cleaned = super().clean()
        # OpenAI: si marcan activo sin key, desactivar en vez de tumbar todo el form.
        enabled = cleaned.get("openai_enabled")
        key = (cleaned.get("openai_api_key") or "").strip()
        if enabled and not key:
            cleaned["openai_enabled"] = False

        g_enabled = cleaned.get("gemini_enabled")
        g_key = (cleaned.get("gemini_api_key") or "").strip()
        if g_enabled and not g_key:
            self.add_error(
                "gemini_api_key",
                "Ingresa una API key de Gemini para activar la verificación visual.",
            )
        conf = cleaned.get("gemini_min_confidence")
        if conf is not None and (conf < 0 or conf > 100):
            self.add_error("gemini_min_confidence", "Debe estar entre 0 y 100.")
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        # Texto visible: lo que está en el formulario es la fuente de verdad.
        obj.openai_api_key = (self.cleaned_data.get("openai_api_key") or "").strip()
        obj.gemini_api_key = (self.cleaned_data.get("gemini_api_key") or "").strip()
        if not (obj.openai_model or "").strip():
            obj.openai_model = IntegrationSettings.OpenAIModel.GPT_56_TERRA
        if not (obj.gemini_model or "").strip():
            obj.gemini_model = IntegrationSettings.GeminiModel.GEMINI_25_FLASH
        if commit:
            obj.save()
        return obj


class PublicAdvisorRegisterForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(
            attrs={"class": "input input-bordered w-full"}
        ),
        required=True,
    )
    password2 = forms.CharField(
        label="Confirmar contraseña",
        widget=forms.PasswordInput(
            attrs={"class": "input input-bordered w-full"}
        ),
        required=True,
    )

    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "email",
            "phone",
            "nit",
            "bank_code",
            "account_type",
            "account_number",
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "last_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "email": forms.EmailInput(attrs={"class": "input input-bordered w-full"}),
            "phone": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "nit": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "bank_code": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "account_type": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "account_number": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
        }

    def _generate_username(self, email, first_name, last_name):
        local = (email or "").split("@")[0]
        if not local:
            local = f"{first_name or ''}{last_name or ''}"
        base = re.sub(r"[^a-z0-9._-]", "", local.lower()) or "asesor"
        candidate = base
        suffix = 1
        while User.objects.filter(username=candidate).exists():
            candidate = f"{base}{suffix}"
            suffix += 1
        return candidate

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Las contraseñas no coinciden.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self._generate_username(
            self.cleaned_data.get("email"),
            self.cleaned_data.get("first_name"),
            self.cleaned_data.get("last_name"),
        )
        user.role = RoleCode.ASESOR
        user.is_active = False
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            role_obj, _created = UserRole.objects.get_or_create(code=RoleCode.ASESOR)
            user.roles.add(role_obj)
        return user


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "email",
            "phone",
            "nit",
            "photo",
            "bank_code",
            "account_type",
            "account_number",
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "last_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "email": forms.EmailInput(attrs={"class": "input input-bordered w-full"}),
            "phone": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "nit": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "photo": forms.ClearableFileInput(attrs={"class": "file-input file-input-bordered w-full"}),
            "bank_code": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "account_type": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "account_number": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
        }


class ChangePasswordForm(forms.Form):
    current_password = forms.CharField(
        label="Contraseña actual",
        widget=forms.PasswordInput(
            attrs={"class": "input input-bordered w-full"}
        ),
    )
    new_password1 = forms.CharField(
        label="Nueva contraseña",
        widget=forms.PasswordInput(
            attrs={"class": "input input-bordered w-full"}
        ),
    )
    new_password2 = forms.CharField(
        label="Confirmar nueva contraseña",
        widget=forms.PasswordInput(
            attrs={"class": "input input-bordered w-full"}
        ),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        password = self.cleaned_data.get("current_password")
        if not self.user.check_password(password):
            raise forms.ValidationError("La contraseña actual no es correcta.")
        return password

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("new_password1")
        p2 = cleaned.get("new_password2")
        if p1 and p2 and p1 != p2:
            self.add_error("new_password2", "Las contraseñas no coinciden.")
        if p1 and len(p1) < 8:
            self.add_error("new_password1", "La contraseña debe tener al menos 8 caracteres.")
        return cleaned


class LoginForm(forms.Form):
    identifier = forms.CharField(
        label="Usuario o correo",
        widget=forms.TextInput(
            attrs={
                "class": "input",
                "placeholder": "usuario@correo.com",
                "autocomplete": "username",
            }
        ),
    )
    password = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(
            attrs={
                "class": "input",
                "placeholder": "••••••••",
                "autocomplete": "current-password",
            }
        ),
    )


class UserCreateForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(
            attrs={"class": "input input-bordered w-full"}
        ),
        required=True,
    )
    password2 = forms.CharField(
        label="Confirmar contraseña",
        widget=forms.PasswordInput(
            attrs={"class": "input input-bordered w-full"}
        ),
        required=True,
    )

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "phone",
            "role",
            "roles",
            "is_active",
        ]
        widgets = {
            "username": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "first_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "last_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "email": forms.EmailInput(attrs={"class": "input input-bordered w-full"}),
            "phone": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "role": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "roles": forms.SelectMultiple(attrs={"class": "select select-bordered w-full"}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Las contraseñas no coinciden.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            self.save_m2m()
        return user


class UserEditForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Nueva contraseña",
        widget=forms.PasswordInput(
            attrs={"class": "input input-bordered w-full"}
        ),
        required=False,
    )
    password2 = forms.CharField(
        label="Confirmar nueva contraseña",
        widget=forms.PasswordInput(
            attrs={"class": "input input-bordered w-full"}
        ),
        required=False,
    )

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "phone",
            "role",
            "roles",
            "is_active",
        ]
        widgets = {
            "username": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "first_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "last_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "email": forms.EmailInput(attrs={"class": "input input-bordered w-full"}),
            "phone": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "role": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "roles": forms.SelectMultiple(attrs={"class": "select select-bordered w-full"}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Las contraseñas no coinciden.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password1")
        if password:
            user.set_password(password)
        if commit:
            user.save()
            self.save_m2m()
        return user
