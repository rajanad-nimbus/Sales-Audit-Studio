import auth

from apps.security import authentication


def test_root_auth_module_reexports_security_contracts():
    assert auth.current_user is authentication.current_user
    assert auth.require_role is authentication.require_role
    assert auth.role_allowed is authentication.role_allowed
    assert auth.AUTH_ENABLED == authentication.AUTH_ENABLED
