"""ACL + menu exclusivity for Almohit Operations Administrator."""
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.almohit_core.services.customer import get_warehouse_assignment
from odoo.addons.almohit_core.services.whatsapp import _claim_log


@tagged("post_install", "-at_install", "almohit_security")
class TestOperationsAdminAccess(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ops_group = cls.env.ref("almohit_core.group_almohit_operations_admin")
        cls.stock_manager_group = cls.env.ref("stock.group_stock_manager")
        cls.system_group = cls.env.ref("base.group_system")
        cls.user_group = cls.env.ref("base.group_user")

        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.ops_user = Users.create({
            "name": "Ops Admin Tester",
            "login": "almohit_ops_admin_tester",
            "email": "ops-admin-tester@example.com",
            "groups_id": [(6, 0, [cls.user_group.id, cls.ops_group.id])],
        })
        cls.plain_user = Users.create({
            "name": "Plain Internal Tester",
            "login": "almohit_plain_internal_tester",
            "email": "plain-internal-tester@example.com",
            "groups_id": [(6, 0, [cls.user_group.id])],
        })
        cls.stock_mgr_user = Users.create({
            "name": "Stock Manager Tester",
            "login": "almohit_stock_mgr_tester",
            "email": "stock-mgr-tester@example.com",
            "groups_id": [(6, 0, [cls.user_group.id, cls.stock_manager_group.id])],
        })
        cls.system_only_user = Users.create({
            "name": "System Only Tester",
            "login": "almohit_system_only_tester",
            "email": "system-only-tester@example.com",
            "groups_id": [(6, 0, [cls.user_group.id, cls.system_group.id])],
        })

        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.env.company.id)],
            limit=1,
        )
        cls.manager_partner = cls.env["res.partner"].create({
            "name": "Ops Admin WH Manager",
            "almohit_app_role": "warehouse_manager",
        })
        cls.template = cls.env["almohit.notification.template"].search([], limit=1)
        cls.seed_log = cls.env["almohit.notification.log"].sudo().create({
            "event_code": "ops_admin_access_seed",
            "res_model": "res.partner",
            "res_id": cls.manager_partner.id,
            "channel": "whatsapp",
            "partner_id": cls.manager_partner.id,
            "recipient": "",
            "recipient_masked": "",
            "message": "seed",
            "status": "queued",
        })
        cls.seed_session = cls.env["almohit.whatsapp.session.log"].sudo().create({
            "event_type": "connected",
            "status": "connected",
            "connected": True,
            "phone": "",
            "message": "seed session",
        })

        cls.menu_templates = cls.env.ref("almohit_core.menu_almohit_whatsapp_templates")
        cls.menu_logs = cls.env.ref("almohit_core.menu_almohit_whatsapp_logs")
        cls.menu_session_logs = cls.env.ref("almohit_core.menu_almohit_whatsapp_session_logs")
        cls.menu_assignments = cls.env.ref("almohit_core.menu_almohit_warehouse_assignment")
        cls.menu_wa_account = cls.env.ref("almohit_core.menu_almohit_whatsapp_account")

    def _visible_menu_ids(self, user):
        menus = self.env["ir.ui.menu"].with_user(user).search([
            ("id", "in", [
                self.menu_templates.id,
                self.menu_logs.id,
                self.menu_session_logs.id,
                self.menu_assignments.id,
                self.menu_wa_account.id,
            ]),
        ])
        return set(menus.ids)

    def test_ops_admin_can_manage_protected_models(self):
        env = self.env(user=self.ops_user)
        self.assertTrue(env["almohit.notification.log"].search_count([
            ("id", "=", self.seed_log.id),
        ]))
        with self.assertRaises(AccessError):
            env["almohit.notification.log"].browse(self.seed_log.id).write({
                "message": "tamper",
            })

        template = env["almohit.notification.template"].browse(self.template.id)
        self.assertTrue(template.exists())
        body_before = template.body
        template.write({"body": body_before + "\n#ops"})
        template.write({"body": body_before})

        preview = env["almohit.notification.preview"].create({
            "template_id": self.template.id,
        })
        self.assertTrue(preview.id)
        preview.unlink()

        self.assertTrue(env["almohit.whatsapp.session.log"].search_count([
            ("id", "=", self.seed_session.id),
        ]))
        with self.assertRaises(AccessError):
            env["almohit.whatsapp.session.log"].browse(self.seed_session.id).unlink()

        assignment = env["almohit.warehouse.assignment"].create({
            "partner_id": self.manager_partner.id,
            "warehouse_ids": [(6, 0, [self.warehouse.id])],
            "default_warehouse_id": self.warehouse.id,
            "perm_receipt": True,
        })
        assignment.write({"perm_issue": True, "active": True})
        assignment_id = assignment.id
        assignment.unlink()
        self.assertFalse(
            self.env["almohit.warehouse.assignment"].sudo().browse(assignment_id).exists()
        )

        visible = self._visible_menu_ids(self.ops_user)
        self.assertEqual(
            visible,
            {
                self.menu_templates.id,
                self.menu_logs.id,
                self.menu_session_logs.id,
                self.menu_assignments.id,
            },
        )

    def test_plain_internal_user_blocked(self):
        env = self.env(user=self.plain_user)
        with self.assertRaises(AccessError):
            env["almohit.notification.log"].search([])
        with self.assertRaises(AccessError):
            env["almohit.notification.template"].search([])
        with self.assertRaises(AccessError):
            env["almohit.notification.preview"].create({"template_id": self.template.id})
        with self.assertRaises(AccessError):
            env["almohit.whatsapp.session.log"].search([])
        with self.assertRaises(AccessError):
            env["almohit.warehouse.assignment"].create({
                "partner_id": self.manager_partner.id,
                "warehouse_ids": [(6, 0, [self.warehouse.id])],
            })
        self.assertFalse(self._visible_menu_ids(self.plain_user))

    def test_stock_manager_cannot_manage_assignments(self):
        env = self.env(user=self.stock_mgr_user)
        with self.assertRaises(AccessError):
            env["almohit.warehouse.assignment"].search([])
        with self.assertRaises(AccessError):
            env["almohit.warehouse.assignment"].create({
                "partner_id": self.manager_partner.id,
                "warehouse_ids": [(6, 0, [self.warehouse.id])],
            })
        # Other stock-facing Almohit ACLs remain for stock managers.
        env["almohit.warehouse.transfer"].check_access_rights("read")
        env["almohit.inventory.session"].check_access_rights("read")
        self.assertNotIn(
            self.menu_assignments.id,
            self._visible_menu_ids(self.stock_mgr_user),
        )

    def test_system_only_does_not_inherit_ops_acls(self):
        env = self.env(user=self.system_only_user)
        with self.assertRaises(AccessError):
            env["almohit.notification.template"].search([])
        with self.assertRaises(AccessError):
            env["almohit.warehouse.assignment"].search([])
        env["almohit.whatsapp.account"].check_access_rights("write")
        visible = self._visible_menu_ids(self.system_only_user)
        self.assertEqual(visible, {self.menu_wa_account.id})
        self.assertNotIn(self.menu_templates.id, visible)
        self.assertNotIn(self.menu_assignments.id, visible)

    def test_automated_notification_path_still_works(self):
        """Services/cron use sudo/superuser; restricted users must not block them."""
        restricted = self.env(user=self.plain_user)
        log, created = _claim_log(
            restricted,
            "ops_admin_auto_claim",
            "res.partner",
            self.manager_partner.id,
            self.manager_partner,
            "auto message",
            "",
        )
        self.assertTrue(created)
        self.assertTrue(log.exists())

        # Cron model method runs as the cron user (superuser in practice).
        cron_env = self.env(user=self.env.ref("base.user_root"))
        cron_env["almohit.notification.log"]._cron_retry_failed()

    def test_warehouse_manager_assignment_lookup_via_sudo(self):
        assignment = self.env["almohit.warehouse.assignment"].sudo().create({
            "partner_id": self.manager_partner.id,
            "warehouse_ids": [(6, 0, [self.warehouse.id])],
            "default_warehouse_id": self.warehouse.id,
            "perm_stock_read": True,
        })
        found = get_warehouse_assignment(self.manager_partner.with_user(self.plain_user))
        self.assertEqual(found, assignment)
