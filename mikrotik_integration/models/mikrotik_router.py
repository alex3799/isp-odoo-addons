import logging

from odoo import api, models, fields
from odoo.exceptions import UserError

from .mikrotik_api import mikrotik_test_connection, mikrotik_notification

_logger = logging.getLogger(__name__)


class MikrotikRouter(models.Model):
    _name = "mikrotik.router"
    _description = "MikroTik Router"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "name"

    active = fields.Boolean(default=True)

    # --- Identity ---
    name = fields.Char(string="Name", required=True, tracking=True,
                       help="Friendly name, e.g. 'CCR2116 - Santiago'")

    # --- Connection ---
    host = fields.Char(string="Host", required=True, tracking=True,
                       help="IP address or hostname, e.g. 10.8.0.2")
    port = fields.Integer(string="Port", default=443)
    username = fields.Char(string="Username", required=True, default="admin")
    password = fields.Char(string="Password",
                           help="Stored in plain text -- required for API calls.")
    use_ssl = fields.Boolean(string="Use SSL / HTTPS", default=True)

    # --- Location ---
    pop_name = fields.Char(string="POP / Office",
                           help="Physical location or point of presence")
    whatsapp_number = fields.Char(string="WhatsApp Number")

    # --- Status (managed by API helper, not user-editable) ---
    status = fields.Selection(
        [
            ("draft", "New"),
            ("online", "Online"),
            ("offline", "Offline"),
            ("error", "Error"),
        ],
        string="Status",
        default="draft",
        readonly=True,
        tracking=True,
    )
    last_sync = fields.Datetime(string="Last Sync", readonly=True)

    # --- Router info (populated by test_connection / refresh) ---
    router_identity = fields.Char(string="Identity", readonly=True)
    router_version = fields.Char(string="RouterOS Version", readonly=True)
    router_model = fields.Char(string="Model", readonly=True)
    router_uptime = fields.Char(string="Uptime", readonly=True)
    cpu_load = fields.Integer(string="CPU Load %", readonly=True)
    free_memory = fields.Float(string="Free Memory MB", readonly=True, digits=(10, 1))
    total_memory = fields.Float(string="Total Memory MB", readonly=True, digits=(10, 1))

    # --- Queue / provisioning settings (Fix 15) ---
    parent_queue_name = fields.Char(
        string="Parent queue name",
        help="Name of the parent queue on RouterOS for hierarchical queuing.",
    )
    address_list_name = fields.Char(
        string="Address list name",
        help="RouterOS address list to add customer IPs to.",
    )

    # --- Notes ---
    note = fields.Text(string="Notes")

    # --- Related ---
    pool_ids = fields.One2many("mikrotik.ip.pool", "router_id", string="Pool Records")
    pool_count = fields.Integer(string="IP Pools", compute="_compute_pool_count")

    @api.depends("pool_ids")
    def _compute_pool_count(self):
        for rec in self:
            rec.pool_count = len(rec.pool_ids)

    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.name} ({rec.host})" if rec.host else rec.name

    def _write_info(self, info):
        """Write mikrotik_test_connection result dict to this router's fields.

        RouterOS returns free-memory and total-memory in bytes; convert to MB.
        """
        free_mb = round(int(info.get("free-memory", 0)) / (1024 * 1024), 1)
        total_mb = round(int(info.get("total-memory", 0)) / (1024 * 1024), 1)
        self.write({
            "router_identity": info.get("identity", ""),
            "router_version": info.get("version", ""),
            "router_model": info.get("board-name", ""),
            "router_uptime": info.get("uptime", ""),
            "cpu_load": int(info.get("cpu-load", 0)),
            "free_memory": free_mb,
            "total_memory": total_mb,
            "last_sync": fields.Datetime.now(),
            "status": "online",
        })

    def action_test_connection(self):
        """Test the API connection and update all router info fields.

        Shows a success or failure notification to the user.
        """
        self.ensure_one()
        try:
            info = mikrotik_test_connection(self)
            self._write_info(info)
            return mikrotik_notification(
                "Connection Successful",
                f"Connected to {info.get('identity', self.name)} -- "
                f"RouterOS {info.get('version', '?')}, {info.get('board-name', '?')}",
            )
        except UserError as e:
            return mikrotik_notification(
                "Connection Failed",
                str(e.args[0]),
                ntype="danger",
                sticky=True,
            )

    def action_refresh_status(self):
        """Silently refresh status and router info. Designed for cron use.

        Failures are logged as warnings rather than raised as errors so that
        one unreachable router does not abort a batch refresh.
        """
        for router in self:
            try:
                info = mikrotik_test_connection(router)
                router._write_info(info)
                _logger.info(
                    "MikroTik refresh OK: %s (%s) -- RouterOS %s",
                    router.name, router.host, info.get("version", "?"),
                )
            except UserError as e:
                _logger.warning(
                    "MikroTik refresh failed for %s (%s): %s",
                    router.name, router.host, e,
                )

    def action_sync_ppp_secrets(self):
        """Pull all PPPoE secrets from the router and upsert into mikrotik.ppp.secret.

        Returns a client notification with sync counts.
        """
        self.ensure_one()
        from .mikrotik_api import mikrotik_rest_call

        secrets_data = mikrotik_rest_call(self, "GET", "ppp/secret", timeout=30)

        if not isinstance(secrets_data, list):
            raise UserError(
                f"Unexpected response from {self.name} for ppp/secret: {secrets_data!r}"
            )

        Secret = self.env["mikrotik.ppp.secret"]
        now = fields.Datetime.now()
        seen_mikrotik_ids = set()
        created = updated = 0

        for item in secrets_data:
            mid = item.get(".id", "")
            if not mid:
                continue
            seen_mikrotik_ids.add(mid)

            vals = {
                "name": item.get("name", ""),
                "password": item.get("password", ""),
                "service": item.get("service", ""),
                "profile": item.get("profile", ""),
                "remote_address": item.get("remote-address", ""),
                "caller_id": item.get("caller-id", ""),
                "comment": item.get("comment", ""),
                "disabled": item.get("disabled", "false").lower() == "true",
                "last_sync": now,
            }

            existing = Secret.search(
                [("mikrotik_id", "=", mid), ("router_id", "=", self.id)], limit=1
            )
            if existing:
                existing.write(vals)
                updated += 1
            else:
                vals.update({"mikrotik_id": mid, "router_id": self.id})
                Secret.create(vals)
                created += 1

        # Remove secrets that no longer exist on the router
        stale = Secret.search(
            [("router_id", "=", self.id), ("mikrotik_id", "not in", list(seen_mikrotik_ids))]
        )
        deleted = len(stale)
        stale.unlink()

        # Link profile_id for all secrets on this router
        all_secrets = Secret.search([("router_id", "=", self.id)])
        all_secrets._link_profile()

        _logger.info(
            "MikroTik PPP sync %s: %d created, %d updated, %d deleted",
            self.name, created, updated, deleted,
        )

        return mikrotik_notification(
            "PPPoE Sync Complete",
            f"Synced {created + updated} PPPoE secrets "
            f"({created} created, {updated} updated, {deleted} deleted) "
            f"from {self.name}",
        )

    def action_sync_ppp_profiles(self):
        """Pull all PPP profiles from the router and upsert into mikrotik.ppp.profile.

        Returns a client notification with sync counts.
        """
        self.ensure_one()
        from .mikrotik_api import mikrotik_rest_call

        profiles_data = mikrotik_rest_call(self, "GET", "ppp/profile", timeout=30)

        if not isinstance(profiles_data, list):
            raise UserError(
                f"Unexpected response from {self.name} for ppp/profile: {profiles_data!r}"
            )

        Profile = self.env["mikrotik.ppp.profile"]
        now = fields.Datetime.now()
        seen_mikrotik_ids = set()
        created = updated = 0

        for item in profiles_data:
            mid = item.get(".id", "")
            if not mid:
                continue
            seen_mikrotik_ids.add(mid)

            vals = {
                "name": item.get("name", ""),
                "local_address": item.get("local-address", ""),
                "remote_address": item.get("remote-address", ""),
                "dns_server": item.get("dns-server", ""),
                "rate_limit": item.get("rate-limit", ""),
                "only_one": item.get("only-one", ""),
                "address_list": item.get("address-list", ""),
                "bridge": item.get("bridge", ""),
                "incoming_filter": item.get("incoming-filter", ""),
                "outgoing_filter": item.get("outgoing-filter", ""),
                "interface_list": item.get("interface-list", ""),
                "change_tcp_mss": item.get("change-tcp-mss", ""),
                "use_compression": item.get("use-compression", ""),
                "use_encryption": item.get("use-encryption", ""),
                "use_mpls": item.get("use-mpls", ""),
                "use_upnp": item.get("use-upnp", ""),
                "comment": item.get("comment", ""),
                "last_sync": now,
            }

            existing = Profile.search(
                [("mikrotik_id", "=", mid), ("router_id", "=", self.id)], limit=1
            )
            if existing:
                existing.write(vals)
                updated += 1
            else:
                vals.update({"mikrotik_id": mid, "router_id": self.id})
                Profile.create(vals)
                created += 1

        # Remove profiles that no longer exist on the router
        stale = Profile.search(
            [("router_id", "=", self.id), ("mikrotik_id", "not in", list(seen_mikrotik_ids))]
        )
        deleted = len(stale)
        stale.unlink()

        _logger.info(
            "MikroTik PPP profile sync %s: %d created, %d updated, %d deleted",
            self.name, created, updated, deleted,
        )

        return mikrotik_notification(
            "PPP Profile Sync Complete",
            f"Synced {created + updated} PPP profiles "
            f"({created} created, {updated} updated, {deleted} deleted) "
            f"from {self.name}",
        )

    def action_sync_queues(self):
        """Pull all simple queues from the router and upsert into mikrotik.queue.simple.

        Returns a client notification with sync counts.
        """
        self.ensure_one()
        from .mikrotik_api import mikrotik_rest_call

        queues_data = mikrotik_rest_call(self, "GET", "queue/simple", timeout=30)

        if not isinstance(queues_data, list):
            raise UserError(
                f"Unexpected response from {self.name} for queue/simple: {queues_data!r}"
            )

        Queue = self.env["mikrotik.queue.simple"]
        now = fields.Datetime.now()
        seen_mikrotik_ids = set()
        created = updated = 0

        for item in queues_data:
            mid = item.get(".id", "")
            if not mid:
                continue
            seen_mikrotik_ids.add(mid)

            vals = {
                "name": item.get("name", ""),
                "target": item.get("target", ""),
                "max_limit": item.get("max-limit", ""),
                "burst_limit": item.get("burst-limit", ""),
                "burst_threshold": item.get("burst-threshold", ""),
                "burst_time": item.get("burst-time", ""),
                "limit_at": item.get("limit-at", ""),
                "priority": item.get("priority", ""),
                "queue_type": item.get("queue", ""),
                "parent": item.get("parent", ""),
                "packet_marks": item.get("packet-marks", ""),
                "disabled": item.get("disabled", "false").lower() == "true",
                "comment": item.get("comment", ""),
                "dynamic": item.get("dynamic", "false").lower() == "true",
                "last_sync": now,
            }

            existing = Queue.search(
                [("mikrotik_id", "=", mid), ("router_id", "=", self.id)], limit=1
            )
            if existing:
                existing.write(vals)
                updated += 1
            else:
                vals.update({"mikrotik_id": mid, "router_id": self.id})
                Queue.create(vals)
                created += 1

        # Remove queues that no longer exist on the router
        stale = Queue.search(
            [("router_id", "=", self.id), ("mikrotik_id", "not in", list(seen_mikrotik_ids))]
        )
        deleted = len(stale)
        stale.unlink()

        _logger.info(
            "MikroTik queue sync %s: %d created, %d updated, %d deleted",
            self.name, created, updated, deleted,
        )

        return mikrotik_notification(
            "Queue Sync Complete",
            f"Synced {created + updated} simple queues "
            f"({created} created, {updated} updated, {deleted} deleted) "
            f"from {self.name}",
        )

    def action_sync_ip_pools(self):
        """Pull all IP pools from the router and upsert into mikrotik.ip.pool.

        Returns a client notification with sync counts.
        """
        self.ensure_one()
        from .mikrotik_api import mikrotik_rest_call

        pools_data = mikrotik_rest_call(self, "GET", "ip/pool", timeout=30)

        if not isinstance(pools_data, list):
            raise UserError(
                f"Unexpected response from {self.name} for ip/pool: {pools_data!r}"
            )

        Pool = self.env["mikrotik.ip.pool"]
        now = fields.Datetime.now()
        seen_mikrotik_ids = set()
        created = updated = 0

        for item in pools_data:
            mid = item.get(".id", "")
            if not mid:
                continue
            seen_mikrotik_ids.add(mid)

            vals = {
                "name": item.get("name", ""),
                "ranges": item.get("ranges", ""),
                "next_pool": item.get("next-pool", ""),
                "available": int(item.get("available", 0) or 0),
                "total": int(item.get("total", 0) or 0),
                "used": int(item.get("used", 0) or 0),
                "last_sync": now,
            }

            existing = Pool.search(
                [("mikrotik_id", "=", mid), ("router_id", "=", self.id)], limit=1
            )
            if existing:
                existing.write(vals)
                updated += 1
            else:
                vals.update({"mikrotik_id": mid, "router_id": self.id})
                Pool.create(vals)
                created += 1

        # Remove pools that no longer exist on the router
        stale = Pool.search(
            [("router_id", "=", self.id), ("mikrotik_id", "not in", list(seen_mikrotik_ids))]
        )
        deleted = len(stale)
        stale.unlink()

        _logger.info(
            "MikroTik IP pool sync %s: %d created, %d updated, %d deleted",
            self.name, created, updated, deleted,
        )

        return mikrotik_notification(
            "IP Pool Sync Complete",
            f"Synced {created + updated} IP pools "
            f"({created} created, {updated} updated, {deleted} deleted) "
            f"from {self.name}",
        )
