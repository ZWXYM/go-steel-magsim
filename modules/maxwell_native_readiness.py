"""Conservative CPU pre-dispatch check; no license checkout or native claim."""
from modules.motor_workbench import license_status


def require_configured_license_connection():
    status=license_status()
    if status.get('available') is not True:
        raise ValueError('Configured license service is unreachable; no native session dispatched')
    return dict(configured_license_TCP_reachable=True,
        native_license_checkout_verified=False,native_design_access_verified=False,
        scope='TCP connectivity is necessary for this local dispatch, not proof of an available Maxwell feature')
