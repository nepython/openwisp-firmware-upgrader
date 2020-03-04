import logging

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from openwisp_controller.config.admin import DeviceAdmin
from openwisp_users.multitenancy import MultitenantAdminMixin
from openwisp_utils.admin import ReadOnlyAdmin, TimeReadonlyAdminMixin

from .models import (BatchUpgradeOperation, Build, Category, DeviceFirmware, FirmwareImage, UpgradeOperation,
                     batch_upgrade_operation)

logger = logging.getLogger(__name__)


class BaseAdmin(MultitenantAdminMixin, TimeReadonlyAdminMixin, admin.ModelAdmin):
    pass


@admin.register(Category)
class CategoryAdmin(BaseAdmin):
    list_display = ('name', 'created', 'modified')
    search_fields = ['name']
    save_on_top = True


class FirmwareImageInline(TimeReadonlyAdminMixin, admin.StackedInline):
    model = FirmwareImage
    extra = 0


@admin.register(Build)
class BuildAdmin(BaseAdmin):
    list_display = ('__str__', 'created', 'modified')
    search_fields = ['name']
    save_on_top = True
    select_related = ('category',)
    ordering = ('-version',)
    inlines = [FirmwareImageInline]
    actions = ['upgrade_selected']
    multitenant_parent = 'category'

    def upgrade_selected(self, request, queryset):
        opts = self.model._meta
        app_label = opts.app_label
        # multiple concurrent batch upgrades are not supported
        # (it's not yet possible to select more builds and upgrade
        #  all of them at the same time)
        if queryset.count() > 1:
            self.message_user(
                request,
                _('Multiple mass upgrades requested but at the moment only '
                  'a single mass upgrade operation at time is supported.'),
                messages.ERROR
            )
            # returning None will display the change list page again
            return None
        upgrade_all = request.POST.get('upgrade_all')
        upgrade_related = request.POST.get('upgrade_related')
        build = queryset.first()
        url = reverse('admin:firmware_upgrader_batchupgradeoperation_changelist')
        # upgrade has been confirmed
        if upgrade_all or upgrade_related:
            from django.utils.safestring import mark_safe
            text = _('Mass upgrade operation started, you can '
                     'track its progress from the <a href="%s">list '
                     'of mass upgrades</a>.') % url
            self.message_user(request, mark_safe(text), messages.SUCCESS)
            batch_upgrade_operation.delay(build_id=build.pk,
                                          firmwareless=upgrade_all)
            # returning None will display the change list page again
            return None
        # upgrade needs to be confirmed
        related_device_fw = build._find_related_device_firmwares(select_devices=True)
        firmwareless_devices = build._find_firmwareless_devices()
        title = _('Confirm mass upgrade operation')
        context = self.admin_site.each_context(request)
        context.update({
            'title': title,
            'related_device_fw': related_device_fw,
            'related_count': len(related_device_fw),
            'firmwareless_devices': firmwareless_devices,
            'firmwareless_count': len(firmwareless_devices),
            'build': build,
            'opts': opts,
            'action_checkbox_name': ACTION_CHECKBOX_NAME,
            'media': self.media,
        })
        request.current_app = self.admin_site.name
        return TemplateResponse(request, [
            'admin/%s/%s/upgrade_selected_confirmation.html' % (app_label, opts.model_name),
            'admin/%s/upgrade_selected_confirmation.html' % app_label,
            'admin/upgrade_selected_confirmation.html'
        ], context)

    upgrade_selected.short_description = 'Mass-upgrade devices related ' \
                                         'to the selected build'


class UpgradeOperationForm(forms.ModelForm):
    class Meta:
        fields = ['device', 'image', 'status', 'log', 'modified']
        labels = {'modified': _('last updated')}


class UpgradeOperationInline(admin.StackedInline):
    model = UpgradeOperation
    form = UpgradeOperationForm
    readonly_fields = UpgradeOperationForm.Meta.fields
    extra = 0

    def last_updated(self, obj):
        return obj.modified

    last_updated.short_description = _('last updated at')

    def has_delete_permission(self, request, obj):
        return False

    def has_add_permission(self, request, obj):
        return False


@admin.register(BatchUpgradeOperation)
class BatchUpgradeOperationAdmin(ReadOnlyAdmin, BaseAdmin):
    list_display = ('build', 'status', 'created', 'modified')
    list_filter = ('status',)
    save_on_top = True
    select_related = ('build',)
    ordering = ('-created',)
    inlines = [UpgradeOperationInline]
    multitenant_parent = 'build'
    fields = [
        'build',
        'status',
        'completed',
        'success_rate',
        'failed_rate',
        'aborted_rate',
        'created',
        'modified'
    ]
    readonly_fields = [
        'completed',
        'success_rate',
        'failed_rate',
        'aborted_rate'
    ]

    def get_readonly_fields(self, request, obj):
        fields = super().get_readonly_fields(request, obj)
        return fields + self.__class__.readonly_fields

    def completed(self, obj):
        return obj.progress_report

    def success_rate(self, obj):
        return self.__get_rate(obj.success_rate)

    def failed_rate(self, obj):
        return self.__get_rate(obj.failed_rate)

    def aborted_rate(self, obj):
        return self.__get_rate(obj.aborted_rate)

    def __get_rate(self, value):
        if value:
            return f'{value}%'
        return 'N/A'

    completed.short_description = _('completed')
    success_rate.short_description = _('success rate')
    failed_rate.short_description = _('failure rate')
    aborted_rate.short_description = _('abortion rate')


class DeviceFirmwareInline(MultitenantAdminMixin, admin.StackedInline):
    model = DeviceFirmware
    exclude = ('created',)
    readonly_fields = ('installed', 'modified')
    verbose_name = _('Device Firmware')
    verbose_name_plural = verbose_name
    extra = 0
    multitenant_shared_relations = ('image',)


DeviceAdmin.inlines.append(DeviceFirmwareInline)
