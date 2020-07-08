from __future__ import absolute_import

from rest_framework import serializers

from sentry.api.fields.multiplechoice import MultipleChoiceField
from sentry.models.projectoption import ProjectOption
from sentry.signals import inbound_filter_toggled
from sentry.utils.data_filters import FilterStatKeys, get_filter_key


def get_all_filters():
    """
    Returns a list of the existing event filters

    An event filter is a function that receives a project_config and an event data payload and returns a tuple
    (should_filter:bool, filter_reason: string | None) representing

    :return: list of registered event filters
    """
    return (
        _localhost_filter,
        _browser_extensions_filter,
        _legacy_browsers_filter,
        _web_crawlers_filter,
    )


def set_filter_state(filter_id, project, state):
    flt = _filter_from_filter_id(filter_id)
    if flt is None:
        raise FilterNotRegistered(filter_id)

    if flt == _legacy_browsers_filter:
        if state is None:
            state = {}

        option_val = "0"
        if "active" in state:
            if state["active"]:
                option_val = "1"
        elif "subfilters" in state and len(state["subfilters"]) > 0:
            option_val = set(state["subfilters"])

        ProjectOption.objects.set_value(
            project=project, key=u"filters:{}".format(filter_id), value=option_val
        )

        return option_val == "1" if option_val in ("0", "1") else option_val

    else:
        # all boolean filters
        if state is None:
            state = {"active": True}

        ProjectOption.objects.set_value(
            project=project,
            key=u"filters:{}".format(filter_id),
            value="1" if state.get("active", False) else "0",
        )

        if state:
            inbound_filter_toggled.send(project=project, sender=flt)

        return state.get("active", False)


def get_filter_state(filter_id, project):
    """
    Returns the filter state

    IMPORTANT: this function accesses the database, it should NEVER be used by the ingestion pipe.
    This api is used by the ProjectFilterDetails and ProjectFilters endpoints
    :param filter_id: the filter Id
    :param project: the project for which we want the filter state
    :return: True if the filter is enabled False otherwise
    :raises: ValueError if filter id not registered
    """
    flt = _filter_from_filter_id(filter_id)
    if flt is None:
        raise FilterNotRegistered(filter_id)

    filter_state = ProjectOption.objects.get_value(
        project=project, key=u"filters:{}".format(flt.id)
    )

    if filter_state is None:
        raise ValueError(
            "Could not find filter state for filter {0}."
            " You need to register default filter state in projectoptions.defaults.".format(
                filter_id
            )
        )

    if flt == _legacy_browsers_filter:
        # special handling for legacy browser state
        if filter_state == "1":
            return True
        if filter_state == "0":
            return False
        return filter_state
    else:
        return filter_state == "1"


class FilterNotRegistered(Exception):
    pass


def _filter_from_filter_id(filter_id):
    """
    Returns the corresponding filter for a filter id or None if no filter with the given id found
    """
    for flt in get_all_filters():
        if flt.spec.id == filter_id:
            return flt
    return None


class _FilterSerializer(serializers.Serializer):
    active = serializers.BooleanField()


class _FilterSpec(object):
    """
    Data associated with a filter, it defines its name, id, default enable state and how its  state is serialized
    in the database
    """

    def __init__(self, id, name, description, serializer_cls=None):
        self.id = id
        self.name = name
        self.description = description
        if serializer_cls is None:
            self.serializer_cls = _FilterSerializer
        else:
            self.serializer_cls = serializer_cls


def _get_filter_settings(project_config, flt):
    """
    Gets the filter options from the relay config or the default option if not specified in the relay config

    :param project_config: the relay config for the request
    :param flt: the filter
    :return: the options for the filter
    """
    filter_settings = project_config.config.get("filterSettings", {})
    return filter_settings.get(get_filter_key(flt), None)


def _is_filter_enabled(project_config, flt):
    filter_options = _get_filter_settings(project_config, flt)

    if filter_options is None:
        raise ValueError("unknown filter", flt.spec.id)

    return filter_options["isEnabled"]


_localhost_filter = _FilterSpec(
    id=FilterStatKeys.LOCALHOST,
    name="Filter out events coming from localhost",
    description="This applies to both IPv4 (``127.0.0.1``) and IPv6 (``::1``) addresses.",
)

_browser_extensions_filter = _FilterSpec(
    id=FilterStatKeys.BROWSER_EXTENSION,
    name="Filter out errors known to be caused by browser extensions",
    description="Certain browser extensions will inject inline scripts and are known to cause errors.",
)


class _LegacyBrowserFilterSerializer(serializers.Serializer):
    active = serializers.BooleanField()
    subfilters = MultipleChoiceField(
        choices=[
            "ie_pre_9",
            "ie9",
            "ie10",
            "ie11",
            "opera_pre_15",
            "android_pre_4",
            "safari_pre_6",
            "opera_mini_pre_8",
        ]
    )


_legacy_browsers_filter = _FilterSpec(
    id=FilterStatKeys.LEGACY_BROWSER,
    name="Filter out known errors from legacy browsers",
    description="Older browsers often give less accurate information, and while they may report valid issues, "
    "the context to understand them is incorrect or missing.",
    serializer_cls=_LegacyBrowserFilterSerializer,
)


_web_crawlers_filter = _FilterSpec(
    id=FilterStatKeys.WEB_CRAWLER,
    name="Filter out known web crawlers",
    description="Some crawlers may execute pages in incompatible ways which then cause errors that"
    " are unlikely to be seen by a normal user.",
)
