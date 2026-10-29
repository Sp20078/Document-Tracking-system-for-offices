-- ============================================================================
--  Reference data for the Office Document Tracking System.
--  Development user accounts are created by scripts/seed_db.py because their
--  passwords must be hashed with Werkzeug at run time.
-- ============================================================================

USE office_document_tracking;

INSERT INTO departments (department_name, description) VALUES
  ('Administration', 'General office administration and records'),
  ('Finance', 'Budgets, invoices and purchase approvals'),
  ('Human Resources', 'Staff records, leave and recruitment'),
  ('Academic Office', 'Examinations, admissions and student records'),
  ('IT Services', 'Systems, support and infrastructure')
ON DUPLICATE KEY UPDATE description = VALUES(description);

INSERT INTO document_types (type_name, description) VALUES
  ('Application', 'Formal request submitted by a person or department'),
  ('Invoice', 'Bill from a supplier or vendor'),
  ('Report', 'Periodic or ad-hoc summary of work'),
  ('Purchase Request', 'Request to buy goods or services'),
  ('Official Letter', 'Outgoing or incoming official correspondence'),
  ('Other', 'Any document that does not fit the categories above')
ON DUPLICATE KEY UPDATE description = VALUES(description);
