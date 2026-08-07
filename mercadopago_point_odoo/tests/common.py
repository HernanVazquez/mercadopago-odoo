"""Shared accounting fixtures for Mercado Pago Point tests."""

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class MercadoPagoPointCommon(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.journal = cls.company_data["default_journal_bank"]
        cls.currency_ars = cls.env.ref("base.ARS")
        cls.currency_ars.active = True
        cls.point_method = cls.env.ref(
            "mercadopago_point_odoo.account_payment_method_mercadopago_point"
        )
        cls.point_lines_created_automatically = cls.env[
            "account.payment.method.line"
        ].search_count([("payment_method_id", "=", cls.point_method.id)])
        cls.point_config = cls.env["mercadopago.point.config"].create({
            "name": "Virtual Point TEST",
            "company_id": cls.company.id,
            "environment": "test",
            "access_token": "TEST-access-token-never-log",
            "terminal_id": "VIRTUAL_POINT_TEST_001",
            "timeout_seconds": 10,
        })
        cls.point_method_line = cls.env["account.payment.method.line"].create({
            "name": "Mercado Pago Point",
            "payment_method_id": cls.point_method.id,
            "journal_id": cls.journal.id,
            "payment_account_id": cls.company_data["default_account_assets"].id,
            "mercadopago_point_config_id": cls.point_config.id,
        })

    def _create_point_payment(self, amount=100.0):
        return self.env["account.payment"].with_company(self.company).create({
            "company_id": self.company.id,
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": self.partner_a.id,
            "amount": amount,
            "currency_id": self.currency_ars.id,
            "journal_id": self.journal.id,
            "payment_method_line_id": self.point_method_line.id,
        })
