from django.shortcuts import render, redirect, get_object_or_404

from .forms import ProjectSettingsForm, HouseTypeForm, FinishCategoryForm, FinishOptionForm
from .models import Project, HouseType, FinishCategory, FinishOption


def project_list(request):
    projects = Project.objects.all().order_by("name")
    return render(request, "inventory/project_list.html", {"projects": projects})


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
    house_types = project.house_types.all().order_by("name")

    if request.method == "POST":
        form = HouseTypeForm(request.POST)
        if form.is_valid():
            house_type = form.save(commit=False)
            house_type.project = project
            house_type.save()
            return redirect("inventory:house_type_list", project_id=project.id)
    else:
        form = HouseTypeForm()

    return render(
        request,
        "inventory/house_type_list.html",
        {"project": project, "house_types": house_types, "form": form},
    )


def house_type_edit(request, project_id, pk):
    project = get_object_or_404(Project, pk=project_id)
    house_type = get_object_or_404(HouseType, pk=pk, project=project)

    if request.method == "POST":
        form = HouseTypeForm(request.POST, instance=house_type)
        if form.is_valid():
            form.save()
            return redirect("inventory:house_type_list", project_id=project.id)
    else:
        form = HouseTypeForm(instance=house_type)

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

    return render(
        request,
        "inventory/finish_category_list.html",
        {"project": project, "categories": categories, "form": form},
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

    return render(
        request,
        "inventory/finish_option_list.html",
        {"project": project, "options": options, "form": form},
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
