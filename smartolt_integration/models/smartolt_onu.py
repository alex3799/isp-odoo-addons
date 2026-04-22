import logging

from odoo import models, fields, api
from odoo.exceptions import UserError

from .smartolt_api import smartolt_api_call, smartolt_api_post, smartolt_notification

_logger = logging.getLogger(__name__)


class SmartoltOnu(models.Model):
    _name = "smartolt.onu"
    _description = "SmartOLT ONU"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "name"

    active = fields.Boolean(default=True)

    # --- Core fields (existing) ---
    name = fields.Char(string="ONU Name", required=True, tracking=True)
    serial_number = fields.Char(
        string="Serial Number", required=True, index=True, tracking=True
    )
    onu_status = fields.Selection(
        [("online", "Online"), ("offline", "Offline"), ("unknown", "Unknown")],
        string="Status",
        default="unknown",
        tracking=True,
    )
    signal_power = fields.Float(string="Rx Power (dBm)", digits=(6, 2))
    customer_name = fields.Char(string="Customer Name")
    smartolt_id = fields.Char(string="SmartOLT ID", copy=False)
    last_sync = fields.Datetime(string="Last Sync", readonly=True)

    # --- OLT relation (replaces old olt_name Char) ---
    olt_id = fields.Many2one(
        "smartolt.olt", string="OLT", ondelete="set null"
    )
    olt_name = fields.Char(
        string="OLT Name", compute="_compute_olt_name", store=True, readonly=False
    )

    # --- ONU identification ---
    external_id = fields.Char(
        string="External ID",
        index=True,
        copy=False,
        help="SmartOLT unique_external_id — used in all per-ONU API calls",
    )
    board = fields.Char(string="Board")
    port = fields.Char(string="Port")
    onu_number = fields.Char(string="ONU Number")

    # --- ONU config ---
    onu_type_id = fields.Many2one(
        "smartolt.onu.type", string="ONU Type", ondelete="set null"
    )
    onu_mode = fields.Selection(
        [("Routing", "Routing"), ("Bridging", "Bridging")],
        string="ONU Mode",
    )
    vlan = fields.Char(string="User VLAN-ID")
    zone_id = fields.Many2one(
        "smartolt.zone", string="Zone", ondelete="set null"
    )
    odb_splitter = fields.Char(string="ODB (Splitter)")
    custom_profile = fields.Char(string="Custom Profile")
    address = fields.Char(string="Address / Comment")

    # --- Signal info ---
    signal_quality = fields.Char(string="Signal Quality")
    signal_1310 = fields.Char(string="Signal 1310 (dBm)")
    signal_1490 = fields.Char(string="Signal 1490 (dBm)")
    # --- GPS coordinates ---
    lat = fields.Float(string="Latitude", digits=(10, 7))
    lng = fields.Float(string="Longitude", digits=(10, 7))


    # --- Speed profiles ---
    upload_speed_profile = fields.Char(string="Profile UP")
    download_speed_profile = fields.Char(string="Profile DOWN")

    # --- Admin status ---
    admin_status = fields.Selection(
        [("enabled", "Enabled"), ("disabled", "Disabled")],
        string="Admin Status",
        default="enabled",
    )

    # --- Customer link ---
    partner_id = fields.Many2one(
        "res.partner", string="Customer", ondelete="set null"
    )

    # --- PPPoE / FreeRADIUS info ---
    pppoe_username = fields.Char(string="PPPoE Username")
    pppoe_password = fields.Char(string="PPPoE Password")
    assigned_ip = fields.Char(string="Assigned IP / Pool")
    mac_address = fields.Char(string="MAC Address")
    freeradius_status = fields.Selection(
        [
            ("active", "Active"),
            ("suspended", "Suspended"),
            ("not_configured", "Not Configured"),
        ],
        string="FreeRADIUS Status",
        default="not_configured",
    )

    _sql_constraints = [
        (
            "serial_number_uniq",
            "unique(serial_number)",
            "Serial number must be unique.",
        ),
    ]

    # --- Computed fields ---
    @api.depends("olt_id", "olt_id.name")
    def _compute_olt_name(self):
        for rec in self:
            if rec.olt_id:
                rec.olt_name = rec.olt_id.name

    # --- Helpers ---
    def _require_external_id(self):
        """Return external_id or raise if missing."""
        self.ensure_one()
        ext_id = self.external_id or self.smartolt_id
        if not ext_id:
            raise UserError("This ONU has no External ID — cannot perform SmartOLT actions.")
        return ext_id

    # --- Status mapping ---
    @api.model
    def _map_status(self, raw_status):
        if not raw_status:
            return "unknown"
        s = str(raw_status).lower()
        if s in ("online", "up", "1", "true", "active"):
            return "online"
        if s in ("offline", "down", "0", "false", "inactive"):
            return "offline"
        return "unknown"

    @api.model
    def _map_admin_status(self, raw):
        if not raw:
            return "enabled"
        return "disabled" if str(raw).lower() in ("disabled", "0", "false") else "enabled"

    # --- Bulk sync ---
    def sync_from_smartolt(self):
        """Fetch all ONUs from SmartOLT and create/update local records."""
        data = smartolt_api_call(self.env, "onu/get_all_onus_details")

        onus = data.get("onus") if isinstance(data, dict) else data
        if not isinstance(onus, list):
            onus = []

        # Pre-fetch lookup maps for relational fields
        OLT = self.env["smartolt.olt"]
        OnuType = self.env["smartolt.onu.type"]
        Zone = self.env["smartolt.zone"]

        olt_map = {r.smartolt_id: r.id for r in OLT.search([]) if r.smartolt_id}
        onu_type_map = {r.smartolt_id: r.id for r in OnuType.search([]) if r.smartolt_id}
        zone_map = {r.smartolt_id: r.id for r in Zone.search([]) if r.smartolt_id}

        # Pre-fetch existing ONUs indexed by external_id and serial_number
        all_onus = self.search([])
        by_ext_id = {}
        by_serial = {}
        for rec in all_onus:
            if rec.external_id:
                by_ext_id[rec.external_id] = rec
            if rec.serial_number:
                by_serial[rec.serial_number] = rec

        now = fields.Datetime.now()
        created = updated = 0

        for item in onus:
            serial = item.get("sn") or ""
            if not serial:
                continue

            ext_id = str(item.get("unique_external_id") or "")

            # Parse signal_power from signal_1310
            try:
                rx_power = float(item.get("signal_1310") or 0.0)
            except (TypeError, ValueError):
                rx_power = 0.0

            # Extract speed info from service_ports
            upload_speed = ""
            download_speed = ""
            svc_ports = item.get("service_ports")
            if isinstance(svc_ports, list) and svc_ports:
                upload_speed = svc_ports[0].get("upload_speed", "")
                download_speed = svc_ports[0].get("download_speed", "")

            # ONU mode
            mode_raw = item.get("mode")
            onu_mode = mode_raw if mode_raw in ("Routing", "Bridging") else False

            vals = {
                "name": item.get("name") or serial,
                "serial_number": serial,
                "external_id": ext_id,
                "onu_status": self._map_status(item.get("status")),
                "signal_power": rx_power,
                "customer_name": item.get("name"),
                "smartolt_id": ext_id,
                "last_sync": now,
                # ONU identification
                "board": item.get("board"),
                "port": item.get("port"),
                "onu_number": item.get("onu"),
                # Config
                "onu_mode": onu_mode,
                "vlan": item.get("service_ports", [{}])[0].get("vlan") if isinstance(item.get("service_ports"), list) and item.get("service_ports") else None,
                "odb_splitter": item.get("odb_name"),
                "custom_profile": item.get("custom_template_name"),
                "address": item.get("address"),
                # Signal
                "signal_quality": item.get("signal") if item.get("signal") != "-" else False,
                "signal_1310": item.get("signal_1310") if item.get("signal_1310") != "-" else False,
                "signal_1490": item.get("signal_1490") if item.get("signal_1490") != "-" else False,
                # Speed
                "upload_speed_profile": upload_speed,
                "download_speed_profile": download_speed,
                # Admin
                "admin_status": self._map_admin_status(item.get("administrative_status")),
                # PPPoE
                "pppoe_username": item.get("username"),
                "pppoe_password": item.get("password"),
                # Relations
                "olt_id": olt_map.get(str(item.get("olt_id") or "")),
                "onu_type_id": onu_type_map.get(str(item.get("onu_type_id") or "")),
                "zone_id": zone_map.get(str(item.get("zone_id") or "")),
            }

            # Match by external_id first, then serial_number
            existing = by_ext_id.get(ext_id) if ext_id else None
            if not existing:
                existing = by_serial.get(serial)

            if existing:
                existing.write(vals)
                updated += 1
                if ext_id:
                    by_ext_id[ext_id] = existing
                by_serial[serial] = existing
            else:
                rec = self.create(vals)
                created += 1
                if ext_id:
                    by_ext_id[ext_id] = rec
                by_serial[serial] = rec

        _logger.info("SmartOLT ONU sync: %d created, %d updated.", created, updated)
        return smartolt_notification(
            "SmartOLT ONU Sync Complete",
            f"{created} ONUs created, {updated} updated.",
        )

    def sync_gps_coordinates(self):
        """Fetch GPS coordinates for all ONUs from SmartOLT.
        Note: SmartOLT rate-limits this endpoint to 3 calls per hour.
        """
        data = smartolt_api_call(self.env, "onu/get_all_onus_gps_coordinates")

        items = data.get("response", [])
        if not isinstance(items, list):
            items = []

        all_onus = self.search([])
        by_ext_id = {r.external_id: r for r in all_onus if r.external_id}
        by_serial = {r.serial_number: r for r in all_onus if r.serial_number}

        updated = skipped = 0
        for item in items:
            try:
                lat = float(item.get("lat") or 0)
                lng = float(item.get("lng") or 0)
            except (TypeError, ValueError):
                skipped += 1
                continue

            if not lat or not lng:
                skipped += 1
                continue

            ext_id = str(item.get("unique_external_id") or "")
            serial = item.get("serial_number") or item.get("sn") or ""

            onu = by_ext_id.get(ext_id) if ext_id else None
            if not onu:
                onu = by_serial.get(serial)

            if onu:
                onu.write({"lat": lat, "lng": lng})
                updated += 1
            else:
                skipped += 1

        _logger.info("SmartOLT ONU GPS sync: %d updated, %d skipped.", updated, skipped)
        return smartolt_notification(
            "ONU GPS Sync",
            f"{updated} ONUs updated with GPS coordinates. {skipped} skipped (zero/null or no match). ⚠️ Maximum 3 calls per hour.",
        )

    # --- Per-ONU refresh ---

    def action_refresh_status(self):
        """Refresh status and signal for this ONU from SmartOLT."""
        ext_id = self._require_external_id()

        now = fields.Datetime.now()
        vals = {"last_sync": now}

        # Fetch status
        try:
            status_data = smartolt_api_call(
                self.env, f"onu/get_onu_status/{ext_id}"
            )
            raw_status = status_data.get("onu_status", "")
            vals["onu_status"] = self._map_status(raw_status)
        except UserError:
            _logger.warning("Could not fetch status for ONU %s", ext_id)

        # Fetch signal
        try:
            signal_data = smartolt_api_call(
                self.env, f"onu/get_onu_signal/{ext_id}"
            )
            sig_1310 = signal_data.get("onu_signal_1310", "")
            sig_1490 = signal_data.get("onu_signal_1490", "")
            sig_label = signal_data.get("onu_signal", "")

            vals["signal_quality"] = sig_label if sig_label != "-" else False
            vals["signal_1310"] = sig_1310 if sig_1310 != "-" else False
            vals["signal_1490"] = sig_1490 if sig_1490 != "-" else False

            try:
                vals["signal_power"] = float(sig_1310) if sig_1310 and sig_1310 != "-" else 0.0
            except (TypeError, ValueError):
                vals["signal_power"] = 0.0
        except UserError:
            _logger.warning("Could not fetch signal for ONU %s", ext_id)

        self.write(vals)
        return smartolt_notification(
            "ONU Refreshed",
            f"Status and signal updated for {self.name}.",
        )

    # --- Open in SmartOLT ---
    def action_open_in_smartolt(self):
        """Open this ONU in SmartOLT web UI by searching its serial number."""
        self.ensure_one()
        if not self.serial_number:
            raise UserError("This ONU has no serial number.")
        ICP = self.env["ir.config_parameter"].sudo()
        base_url = ICP.get_param(
            "smartolt.api_url", "https://your-tenant.smartolt.com"
        ).rstrip("/")
        url = f"{base_url}/onu/configured?free_text={self.serial_number}&sort_by=id&sort_order=desc"
        return {
            "type": "ir.actions.act_url",
            "url": url,
            "target": "new",
        }

    # --- Action buttons ---
    def action_reboot(self):
        """Send reboot command to ONU via SmartOLT API."""
        ext_id = self._require_external_id()
        smartolt_api_post(self.env, f"onu/reboot/{ext_id}")
        return smartolt_notification(
            "ONU Reboot", f"Reboot command sent to {self.name}."
        )

    def action_enable(self):
        """Enable ONU via SmartOLT API."""
        ext_id = self._require_external_id()
        smartolt_api_post(self.env, f"onu/enable/{ext_id}")
        self.write({"admin_status": "enabled"})
        return smartolt_notification(
            "ONU Enabled", f"{self.name} has been enabled."
        )

    def action_disable(self):
        """Disable ONU via SmartOLT API."""
        ext_id = self._require_external_id()
        smartolt_api_post(self.env, f"onu/disable/{ext_id}")
        self.write({"admin_status": "disabled"})
        return smartolt_notification(
            "ONU Disabled", f"{self.name} has been disabled.",
            ntype="warning",
        )

    def action_resync_config(self):
        """Resync ONU configuration via SmartOLT API."""
        ext_id = self._require_external_id()
        smartolt_api_post(self.env, f"onu/resync/{ext_id}")
        return smartolt_notification(
            "Config Resync", f"Config resync command sent to {self.name}."
        )

    def action_delete_onu(self):
        """Delete ONU from SmartOLT and archive the Odoo record."""
        ext_id = self._require_external_id()
        smartolt_api_post(self.env, f"onu/delete/{ext_id}")
        self.write({"active": False})
        return smartolt_notification(
            "ONU Deleted",
            f"{self.name} has been deleted from SmartOLT and archived in Odoo.",
            ntype="warning",
            sticky=True,
        )
