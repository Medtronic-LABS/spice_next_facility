"""Demo seed over the API: refused unless the deployment opts in, System Manager only, queued as a job."""

import os
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from spice_facility.demo import api


class TestDemoSeedApi(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	def opted(self, value):
		environ = {k: v for k, v in os.environ.items() if k != api.FLAG}
		if value is not None:
			environ[api.FLAG] = value
		return patch.dict(os.environ, environ, clear=True), patch.dict(frappe.conf, {api.FLAG.lower(): None})

	def test_refused_unless_the_deployment_opts_in(self):
		for value in (None, "0", "", "no"):
			env, conf = self.opted(value)
			with env, conf, patch("frappe.enqueue") as enqueue:
				with self.assertRaises(frappe.PermissionError, msg=repr(value)):
					api.seed(patients=5)
				enqueue.assert_not_called()

	def test_only_system_managers(self):
		env, conf = self.opted("1")
		frappe.set_user("Guest")
		with env, conf, patch("frappe.enqueue") as enqueue:
			with self.assertRaises(frappe.PermissionError):
				api.seed(patients=5)
			enqueue.assert_not_called()

	def test_queues_one_background_job(self):
		env, conf = self.opted("1")
		with env, conf, patch("frappe.enqueue", return_value=object()) as enqueue:
			result = api.seed(patients="50")
		self.assertEqual(result, {"queued": True, "job_id": "spice-demo-seed", "patients": 50})
		args, kwargs = enqueue.call_args
		self.assertEqual(args, (api.JOB,))
		self.assertTrue(kwargs["deduplicate"])
		self.assertEqual((kwargs["queue"], kwargs["patients"]), ("long", 50))

	def test_patient_count_is_bounded(self):
		env, conf = self.opted("1")
		with env, conf, patch("frappe.enqueue"):
			for bad in (0, api.MAX_PATIENTS + 1):
				with self.assertRaises(frappe.ValidationError):
					api.seed(patients=bad)
