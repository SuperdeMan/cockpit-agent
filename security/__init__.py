"""安全与权限模块。提供权限引擎、scope 常量、审计、注入防护。"""
from .scopes import ALL_SCOPES, TRUST_LEVEL_CAPS, is_scope_covered
from .permission import PermissionEngine, AuthContext, Decision
from .audit import AuditLogger, AuditEvent
