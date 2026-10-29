-- ============================================================================
--  Office Document Tracking System - core schema
--  Tested with MySQL 8.0 / 8.4
--
--  Apply with:
--     mysql -u root -p < sql/schema.sql
--  or:
--     python scripts/init_db.py
-- ============================================================================

CREATE DATABASE IF NOT EXISTS office_document_tracking
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE office_document_tracking;

-- Drop in reverse dependency order so the script can be re-run.
DROP VIEW IF EXISTS v_department_status_summary;
DROP VIEW IF EXISTS v_pending_documents;
DROP TABLE IF EXISTS attachments;
DROP TABLE IF EXISTS approvals;
DROP TABLE IF EXISTS document_tracking;
DROP TABLE IF EXISTS documents;
DROP TABLE IF EXISTS document_types;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS departments;

-- ---------------------------------------------------------------------------
-- 1. departments
-- ---------------------------------------------------------------------------
CREATE TABLE departments (
  department_id   INT AUTO_INCREMENT PRIMARY KEY,
  department_name VARCHAR(120) NOT NULL,
  description     TEXT NULL,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_departments_name UNIQUE (department_name)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------------
-- 2. users
--    role: admin | records_officer | employee | manager
-- ---------------------------------------------------------------------------
CREATE TABLE users (
  user_id       INT AUTO_INCREMENT PRIMARY KEY,
  full_name     VARCHAR(120) NOT NULL,
  email         VARCHAR(190) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role          ENUM('admin', 'records_officer', 'employee', 'manager') NOT NULL,
  department_id INT NULL,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_users_email UNIQUE (email),
  CONSTRAINT fk_users_department
    FOREIGN KEY (department_id) REFERENCES departments (department_id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  INDEX idx_users_role (role),
  INDEX idx_users_department (department_id)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------------
-- 3. document_types
-- ---------------------------------------------------------------------------
CREATE TABLE document_types (
  document_type_id INT AUTO_INCREMENT PRIMARY KEY,
  type_name        VARCHAR(120) NOT NULL,
  description      TEXT NULL,
  CONSTRAINT uq_document_types_name UNIQUE (type_name)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------------
-- 4. documents
--    document_number is generated from the auto-increment id (DOC-YYYY-0001)
-- ---------------------------------------------------------------------------
CREATE TABLE documents (
  document_id           INT AUTO_INCREMENT PRIMARY KEY,
  document_number       VARCHAR(32) NOT NULL,
  title                 VARCHAR(200) NOT NULL,
  description           TEXT NULL,
  document_type_id      INT NOT NULL,
  submitted_by          INT NOT NULL,
  current_department_id INT NULL,
  assigned_to           INT NULL,
  submission_date       DATE NOT NULL,
  due_date              DATE NULL,
  priority              ENUM('Low', 'Normal', 'High', 'Urgent') NOT NULL DEFAULT 'Normal',
  status                ENUM('Submitted', 'Pending', 'In Progress', 'Forwarded',
                             'Approved', 'Rejected', 'Closed') NOT NULL DEFAULT 'Submitted',
  created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  archived              BOOLEAN NOT NULL DEFAULT FALSE,
  CONSTRAINT uq_documents_number UNIQUE (document_number),
  CONSTRAINT fk_documents_type
    FOREIGN KEY (document_type_id) REFERENCES document_types (document_type_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_documents_submitter
    FOREIGN KEY (submitted_by) REFERENCES users (user_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_documents_department
    FOREIGN KEY (current_department_id) REFERENCES departments (department_id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_documents_assignee
    FOREIGN KEY (assigned_to) REFERENCES users (user_id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  -- A document can never be due before it was submitted.
  CONSTRAINT chk_documents_due_date CHECK (due_date IS NULL OR due_date >= submission_date),
  INDEX idx_documents_status (status),
  INDEX idx_documents_department_status (current_department_id, status),
  INDEX idx_documents_submission_date (submission_date),
  INDEX idx_documents_assigned (assigned_to, status),
  INDEX idx_documents_archived (archived)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------------
-- 5. document_tracking  (append-only audit trail)
-- ---------------------------------------------------------------------------
CREATE TABLE document_tracking (
  tracking_id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  document_id        INT NOT NULL,
  action             VARCHAR(80) NOT NULL,
  from_department_id INT NULL,
  to_department_id   INT NULL,
  from_user_id       INT NULL,
  to_user_id         INT NULL,
  actor_id           INT NOT NULL,
  remarks            TEXT NULL,
  action_timestamp   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_tracking_document
    FOREIGN KEY (document_id) REFERENCES documents (document_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_tracking_from_department
    FOREIGN KEY (from_department_id) REFERENCES departments (department_id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_tracking_to_department
    FOREIGN KEY (to_department_id) REFERENCES departments (department_id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_tracking_from_user
    FOREIGN KEY (from_user_id) REFERENCES users (user_id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_tracking_to_user
    FOREIGN KEY (to_user_id) REFERENCES users (user_id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_tracking_actor
    FOREIGN KEY (actor_id) REFERENCES users (user_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  INDEX idx_tracking_document_time (document_id, action_timestamp),
  INDEX idx_tracking_timestamp (action_timestamp)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------------
-- 6. approvals
--    Pending decisions have decision_timestamp = NULL, decided rows do not.
-- ---------------------------------------------------------------------------
CREATE TABLE approvals (
  approval_id        INT AUTO_INCREMENT PRIMARY KEY,
  document_id        INT NOT NULL,
  approver_id        INT NOT NULL,
  decision           ENUM('pending', 'approved', 'rejected') NOT NULL DEFAULT 'pending',
  remarks            TEXT NULL,
  decision_timestamp TIMESTAMP NULL DEFAULT NULL,
  created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_approvals_document
    FOREIGN KEY (document_id) REFERENCES documents (document_id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_approvals_approver
    FOREIGN KEY (approver_id) REFERENCES users (user_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT chk_approvals_timestamp CHECK (
    (decision = 'pending' AND decision_timestamp IS NULL)
    OR (decision <> 'pending' AND decision_timestamp IS NOT NULL)
  ),
  CONSTRAINT chk_approvals_rejection_remarks CHECK (
    decision <> 'rejected'
    OR (remarks IS NOT NULL AND CHAR_LENGTH(TRIM(remarks)) > 0)
  ),
  INDEX idx_approvals_approver_decision (approver_id, decision),
  INDEX idx_approvals_document (document_id)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------------
-- 7. attachments  (bytes live in the protected uploads/ folder)
-- ---------------------------------------------------------------------------
CREATE TABLE attachments (
  attachment_id    INT AUTO_INCREMENT PRIMARY KEY,
  document_id      INT NOT NULL,
  uploaded_by      INT NOT NULL,
  original_filename VARCHAR(255) NOT NULL,
  stored_filename  VARCHAR(255) NOT NULL,
  storage_path     VARCHAR(500) NOT NULL,
  file_size        BIGINT NOT NULL,
  uploaded_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_attachments_stored_name UNIQUE (stored_filename),
  CONSTRAINT fk_attachments_document
    FOREIGN KEY (document_id) REFERENCES documents (document_id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_attachments_user
    FOREIGN KEY (uploaded_by) REFERENCES users (user_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT chk_attachments_size CHECK (file_size > 0 AND file_size <= 52428800),
  INDEX idx_attachments_document (document_id)
) ENGINE = InnoDB;
