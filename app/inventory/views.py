from django.shortcuts import render, redirect, get_object_or_404

from core.listing import paginate, search

from .forms import ProjectSettingsForm, HouseTypeForm, FinishCategoryForm, FinishOptionForm
from .models import Project, HouseType, FinishCategory, FinishOption


def _listing_context(request, queryset, search_fields, *, per_page=25, extra_active=False):
    """Aplica ``q`` + paginación y devuelve el contexto común de los listados."""
    query = (request.GET.get("q") or "").strip()
    page_obj = paginate(request, search(queryset, query, search_fields), per_page=per_page)
    return {
        "page_obj": page_obj,
        "query": query,
        "filters_active": bool(query) or extra_active,
    }


def project_list(request):
    projects = Project.objects.all().order_by("name")
    listing = _listing_context(request, projects, ["name", "city"], per_page=24)
    return render(
        request,
        "inventory/project_list.html",
        {"projects": listing["page_obj"].object_list, **listing},
    )


def project_settings(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    if request.method == "POST":
        form = ProjectSettingsForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            return redirect("inventory:project_settings", project_id=project.id)
    else:
        form = ProjectSettingsForm(instance=project)
    return render(request, "inventory/project_settings.html", {"project": project, "form": form})


def house_type_list(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    house_types = project.house_types.prefetch_related("required_finish_categories").all().order_by("name")

    if request.method == "POST":
        form = HouseTypeForm(request.POST, project=project)
        if form.is_valid():
            house_type = form.save(commit=False)
            house_type.project = project
            house_type.save()
            form.save_m2m()
            return redirect("inventory:house_type_list", project_id=project.id)
    else:
        form = HouseTypeForm(project=project)

    listing = _listing_context(request, house_types, ["name", "description"])
    return render(
        request,
        "inventory/house_type_list.html",
        {
            "project": project,
            "house_types": listing["page_obj"].object_list,
            "form": form,
            **listing,
        },
    )


def house_type_edit(request, project_id, pk):
    project = get_object_or_404(Project, pk=project_id)
    house_type = get_object_or_404(HouseType, pk=pk, project=project)

    if request.method == "POST":
        form = HouseTypeForm(request.POST, instance=house_type, project=project)
        if form.is_valid():
            form.save()
            return redirect("inventory:house_type_list", project_id=project.id)
    else:
        form = HouseTypeForm(instance=house_type, project=project)

    return render(
        request,
        "inventory/house_type_form.html",
        {"project": project, "form": form, "house_type": house_type},
    )


def house_type_delete(request, project_id, pk):
    project = get_object_or_404(Project, pk=project_id)
    house_type = get_object_or_404(HouseType, pk=pk, project=project)

    if request.method == "POST":
        house_type.delete()
        return redirect("inventory:house_type_list", project_id=project.id)

    return render(
        request,
        "inventory/confirm_delete.html",
        {
            "project": project,
            "object_name": house_type.name,
            "cancel_url": "inventory:house_type_list",
        },
    )


def finish_category_list(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    categories = project.finish_categories.all().order_by("order", "name")

    if request.method == "POST":
        form = FinishCategoryForm(request.POST)
        if form.is_valid():
            category = form.save(commit=False)
            category.project = project
            category.save()
            return redirect("inventory:finish_category_list", project_id=project.id)
    else:
        form = FinishCategoryForm()

    estado = (request.GET.get("estado") or "").strip()
    if estado in ("activa", "inactiva"):
        categories = categories.filter(is_active=(estado == "activa"))
    else:
        estado = ""
    listing = _listing_context(request, categories, ["name"], extra_active=bool(estado))
    return render(
        request,
        "inventory/finish_category_list.html",
        {
            "project": project,
            "categories": listing["page_obj"].object_list,
            "form": form,
            "estado": estado,
            **listing,
        },
    )


def finish_category_edit(request, project_id, pk):
    project = get_object_or_404(Project, pk=project_id)
    category = get_object_or_404(FinishCategory, pk=pk, project=project)

    if request.method == "POST":
        form = FinishCategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            return redirect("inventory:finish_category_list", project_id=project.id)
    else:
        form = FinishCategoryForm(instance=category)

    return render(
        request,
        "inventory/finish_category_form.html",
        {"project": project, "form": form, "category": category},
    )


def finish_category_delete(request, project_id, pk):
    project = get_object_or_404(Project, pk=project_id)
    category = get_object_or_404(FinishCategory, pk=pk, project=project)

    if request.method == "POST":
        category.delete()
        return redirect("inventory:finish_category_list", project_id=project.id)

    return render(
        request,
        "inventory/confirm_delete.html",
        {
            "project": project,
            "object_name": category.name,
            "cancel_url": "inventory:finish_category_list",
        },
    )


def finish_option_list(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    options = FinishOption.objects.filter(category__project=project).select_related("category").order_by("category__order", "name")

    if request.method == "POST":
        form = FinishOptionForm(request.POST)
        form.fields["category"].queryset = project.finish_categories.all().order_by("order", "name")
        if form.is_valid():
            form.save()
            return redirect("inventory:finish_option_list", project_id=project.id)
    else:
        form = FinishOptionForm()
        form.fields["category"].queryset = project.finish_categories.all().order_by("order", "name")

    categories = list(project.finish_categories.all().order_by("order", "name"))
    try:
        category_id = int(request.GET.get("categoria") or 0)
    except ValueError:
        category_id = 0
    category = next((c for c in categories if c.pk == category_id), None)
    if category:
        options = options.filter(category=category)
    listing = _listing_context(
        request, options, ["name", "unit", "category__name"], extra_active=bool(category)
    )
    return render(
        request,
        "inventory/finish_option_list.html",
        {
            "project": project,
            "options": listing["page_obj"].object_list,
            "form": form,
            "categories": categories,
            "category": category,
            **listing,
        },
    )


def finish_option_edit(request, project_id, pk):
    project = get_object_or_404(Project, pk=project_id)
    option = get_object_or_404(FinishOption, pk=pk, category__project=project)

    if request.method == "POST":
        form = FinishOptionForm(request.POST, instance=option)
        form.fields["category"].queryset = project.finish_categories.all().order_by("order", "name")
        if form.is_valid():
            form.save()
            return redirect("inventory:finish_option_list", project_id=project.id)
    else:
        form = FinishOptionForm(instance=option)
        form.fields["category"].queryset = project.finish_categories.all().order_by("order", "name")

    return render(
        request,
        "inventory/finish_option_form.html",
        {"project": project, "form": form, "option": option},
    )


def finish_option_delete(request, project_id, pk):
    project = get_object_or_404(Project, pk=project_id)
    option = get_object_or_404(FinishOption, pk=pk, category__project=project)

    if request.method == "POST":
        option.delete()
        return redirect("inventory:finish_option_list", project_id=project.id)

    return render(
        request,
        "inventory/confirm_delete.html",
        {
            "project": project,
            "object_name": option.name,
            "cancel_url": "inventory:finish_option_list",
        },
    )
