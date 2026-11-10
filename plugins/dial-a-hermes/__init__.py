"""Dial-a-Hermes — use Hermes from a basic feature phone over USSD (+ SMS)."""

from __future__ import annotations


def register(ctx) -> None:
    """Plugin entry point. Keeps the import light: the adapter loads with the platform."""
    import dataclasses

    from gateway.platform_registry import PlatformEntry

    from . import adapter

    kwargs = dict(
        name=adapter.PLATFORM_NAME,
        label="USSD (Dial-a-Hermes)",
        adapter_factory=adapter.UssdAdapter,
        check_fn=adapter.check_requirements,
        validate_config=adapter.validate_config,
        required_env=["USSD_ALLOWED_USERS", "USSD_CALLBACK_TOKEN"],
        install_hint="aiohttp is required (declared in this plugin's pyproject.toml)",
        env_enablement_fn=adapter._env_enablement,
        allowed_users_env="USSD_ALLOWED_USERS",
        cron_deliver_env_var="USSD_HOME_CHANNEL",
        max_message_length=adapter.UssdAdapter.MAX_MESSAGE_LENGTH,
        pii_safe=True,
        emoji="📟",
        allow_update_command=False,
        platform_hint=adapter.PLATFORM_HINT,
    )
    known = {f.name for f in dataclasses.fields(PlatformEntry)}
    for key, value in (
        ("display_tier", "minimal"),
        ("allowlist_suppresses_pairing", True),
        ("dashboard_description", "Use Hermes from any feature phone over USSD, with keypad approvals."),
        ("docs_url", "https://github.com/ebohdarlington123-cmyk/dial-a-hermes"),
        ("dashboard_env_vars", ("USSD_ALLOWED_USERS", "USSD_CALLBACK_TOKEN")),
        ("dashboard_required_env", ("USSD_ALLOWED_USERS", "USSD_CALLBACK_TOKEN")),
    ):
        if key in known:
            kwargs[key] = value
    ctx.register_platform(**kwargs)
