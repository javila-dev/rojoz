import re

from django import forms

from .models import IntegrationSettings, User, UserRole, RoleCode


class IntegrationSettingsForm(forms.ModelForm):
    class Meta:
        model = IntegrationSettings
        fields = ["projects_api_url", "projects_api_key"]
        widgets = {
            "projects_api_url": forms.URLInput(attrs={"class": "input input-bordered w-full"}),
            "projects_api_key": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
        }


class PublicAdvisorRegisterForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Contrasena",
        widget=forms.PasswordInput(attrs={
            "class": "input input-bordered w-full",
            "placeholder": "Minimo 8 caracteres",
            "id": "id_password1",
        }),
        required=True,
    )
    password2 = forms.CharField(
        label="Confirmar contrasena",
        widget=forms.PasswordInput(attrs={
            "class": "input input-bordered w-full",
            "placeholder": "Repite tu contrasena",
            "id": "id_password2",
        }),
        required=True,
    )

    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "email",
            "phone",
            "bank_code",
            "account_type",
            "account_number",
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "ej. Juan Carlos"}),
            "last_name": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "ej. Perez Lopez"}),
            "email": forms.EmailInput(attrs={"class": "input input-bordered w-full", "placeholder": "ej. juan@email.com"}),
            "phone": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "ej. 3001234567"}),
            "bank_code": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "account_type": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "account_number": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "Sin guiones ni espacios"}),
        }

    def _generate_username(self, email, first_name, last_name):
        """Genera un username unico a partir del email."""
        # Usar la parte antes del @ del email
        base = email.split("@")[0] if email else ""
        if not base and first_name and last_name:
            # Fallback: primera letra del nombre + apellido
            base = (first_name[0] + last_name).lower()
        base = re.sub(r"[^a-zA-Z0-9._-]", "", base)[:30] or "usuario"

        username = base
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base}{counter}"
            counter += 1
        return username

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
            user.email, user.first_name, user.last_name,
        )
        user.role = RoleCode.ASESOR
        user.is_active = False  # Requiere aprobacion de un admin
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            role_obj, _created = UserRole.objects.get_or_create(code=RoleCode.ASESOR)
            user.roles.add(role_obj)
        return user


class ProfileForm(forms.ModelForm):
    """Formulario para que el usuario edite su propio perfil."""

    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "email",
            "phone",
            "photo",
            "bank_code",
            "account_type",
            "account_number",
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "last_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "email": forms.EmailInput(attrs={"class": "input input-bordered w-full"}),
            "phone": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "ej. 3001234567"}),
            "photo": forms.ClearableFileInput(attrs={"class": "file-input file-input-bordered w-full"}),
            "bank_code": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "account_type": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "account_number": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "Sin guiones ni espacios"}),
        }


class ChangePasswordForm(forms.Form):
    """Formulario para cambio de contrasena desde el perfil."""
    current_password = forms.CharField(
        label="Contrasena actual",
        widget=forms.PasswordInput(attrs={
            "class": "input input-bordered w-full",
            "autocomplete": "current-password",
        }),
    )
    new_password1 = forms.CharField(
        label="Nueva contrasena",
        widget=forms.PasswordInput(attrs={
            "class": "input input-bordered w-full",
            "placeholder": "Minimo 8 caracteres",
            "autocomplete": "new-password",
        }),
    )
    new_password2 = forms.CharField(
        label="Confirmar nueva contrasena",
        widget=forms.PasswordInput(attrs={
            "class": "input input-bordered w-full",
            "autocomplete": "new-password",
        }),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        current = self.cleaned_data.get("current_password")
        if not self.user.check_password(current):
            raise forms.ValidationError("La contrasena actual es incorrecta.")
        return current

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("new_password1")
        p2 = cleaned.get("new_password2")
        if p1 and p2 and p1 != p2:
            self.add_error("new_password2", "Las contrasenas no coinciden.")
        if p1 and len(p1) < 8:
            self.add_error("new_password1", "La contrasena debe tener al menos 8 caracteres.")
        return cleaned


class LoginForm(forms.Form):
    identifier = forms.CharField(
        label="Usuario o correo",
        widget=forms.TextInput(attrs={
            "class": "input input-bordered w-full",
            "placeholder": "tu_usuario o correo@email.com",
            "autocomplete": "username",
        }),
    )
    password = forms.CharField(
        label="Contrasena",
        widget=forms.PasswordInput(attrs={
            "class": "input input-bordered w-full",
            "placeholder": "Tu contrasena",
            "autocomplete": "current-password",
        }),
    )


class UserCreateForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Contrasena",
        widget=forms.PasswordInput(attrs={
            "class": "input input-bordered w-full",
            "placeholder": "Minimo 8 caracteres",
            "autocomplete": "new-password",
        }),
        required=True,
    )
    password2 = forms.CharField(
        label="Confirmar contrasena",
        widget=forms.PasswordInput(attrs={
            "class": "input input-bordered w-full",
            "placeholder": "Repite la contrasena",
            "autocomplete": "new-password",
        }),
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
            self.add_error("password2", "Las contrasenas no coinciden.")
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
        label="Nueva contrasena",
        widget=forms.PasswordInput(attrs={
            "class": "input input-bordered w-full",
            "placeholder": "Dejar en blanco para mantener",
            "autocomplete": "new-password",
        }),
        required=False,
    )
    password2 = forms.CharField(
        label="Confirmar contrasena",
        widget=forms.PasswordInput(attrs={
            "class": "input input-bordered w-full",
            "placeholder": "Repite la contrasena",
            "autocomplete": "new-password",
        }),
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
        if (password1 or password2) and password1 != password2:
            self.add_error("password2", "Las contrasenas no coinciden.")
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
