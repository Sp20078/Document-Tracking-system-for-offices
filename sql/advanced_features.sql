-- ============================================================================
--  Office Document Tracking System - advanced DBMS features
--  MySQL 8.0+ (tested on 8.4)
--
--  Apply with:
--     mysql -u root -p office_document_tracking < sql/advanced_features.sql
--  or:
--     python scripts/init_db.py
--
--  OWNERSHIP OF AUDIT EVENTS (avoids duplicated tracking rows)
--  ---------------------------------------------------------------------------
--  * "Document registered"          -> written by trigger trg_documents_after_insert
--                                      (a single INSERT on documents always creates it).
--  * "Status changed / archived"    -> written by trigger trg_documents_after_update.
--                                      The Flask app sets three session variables
--                                      (@tracking_action, @tracking_actor_id,
--                                      @tracking_remarks) right before its UPDATE, so
--                                      the trigger stores the rich action name and
--                                      remarks. If somebody changes a status manually
--                                      in SQL, the trigger still writes a generic row.
--  * "Remarks added" and every other  -> written directly by the Flask application
--    non-status event (e.g. note)        (these do NOT change documents.status, so the
--                                        trigger does not fire and cannot duplicate).
-- ============================================================================

USE office_document_tracking;

DROP TRIGGER IF EXISTS trg_documents_after_insert;
DROP TRIGGER IF EXISTS trg_documents_after_update;
DROP PROCEDURE IF EXISTS sp_register_document;
DROP PROCEDURE IF EXISTS sp_forward_document;
DROP FUNCTION IF EXISTS fn_pending_days;
DROP VIEW IF EXISTS v_pending_documents;
DROP VIEW IF EXISTS v_department_status_summary;

-- ---------------------------------------------------------------------------
-- FUNCTION: fn_pending_days
--   "Pending" means the document is still waiting for somebody to act:
--   status Submitted / Pending / In Progress / Forwarded and not archived.
--   Elapsed days are counted from the most recent tracking event (falling back
--   to the submission date). Closed / approved / rejected / archived documents
--   return 0.
-- ---------------------------------------------------------------------------
DELIMITER $$

CREATE FUNCTION fn_pending_days(p_document_id INT)
RETURNS INT
READS SQL DATA
BEGIN
  DECLARE v_status VARCHAR(20);
  DECLARE v_archived TINYINT;
  DECLARE v_submission DATE;
  DECLARE v_last_activity TIMESTAMP;

  SELECT status, archived, submission_date
    INTO v_status, v_archived, v_submission
    FROM documents
   WHERE document_id = p_document_id;

  IF v_status IS NULL OR v_archived = 1
     OR v_status NOT IN ('Submitted', 'Pending', 'In Progress', 'Forwarded') THEN
    RETURN 0;
  END IF;

  SELECT MAX(action_timestamp) INTO v_last_activity
    FROM document_tracking
   WHERE document_id = p_document_id;

  RETURN GREATEST(DATEDIFF(CURDATE(), COALESCE(DATE(v_last_activity), v_submission)), 0);
END$$

-- ---------------------------------------------------------------------------
-- VIEW 1: v_pending_documents
-- ---------------------------------------------------------------------------
CREATE VIEW v_pending_documents AS
SELECT
  d.document_id,
  d.document_number,
  d.title,
  d.status,
  d.priority,
  d.submission_date,
  d.due_date,
  dt.type_name                AS document_type,
  dept.department_name        AS current_department,
  u.full_name                 AS assigned_employee,
  fn_pending_days(d.document_id) AS pending_days
FROM documents d
JOIN document_types dt ON dt.document_type_id = d.document_type_id
LEFT JOIN departments dept ON dept.department_id = d.current_department_id
LEFT JOIN users u ON u.user_id = d.assigned_to
WHERE d.status IN ('Submitted', 'Pending', 'In Progress', 'Forwarded')
  AND d.archived = FALSE$$

-- ---------------------------------------------------------------------------
-- VIEW 2: v_department_status_summary
-- ---------------------------------------------------------------------------
CREATE VIEW v_department_status_summary AS
SELECT
  dept.department_id,
  dept.department_name,
  COUNT(d.document_id)                                          AS total_documents,
  COALESCE(SUM(CASE WHEN d.status = 'Submitted'   THEN 1 END), 0) AS submitted,
  COALESCE(SUM(CASE WHEN d.status = 'Pending'     THEN 1 END), 0) AS pending,
  COALESCE(SUM(CASE WHEN d.status = 'In Progress' THEN 1 END), 0) AS in_progress,
  COALESCE(SUM(CASE WHEN d.status = 'Forwarded'   THEN 1 END), 0) AS forwarded,
  COALESCE(SUM(CASE WHEN d.status = 'Approved'    THEN 1 END), 0) AS approved,
  COALESCE(SUM(CASE WHEN d.status = 'Rejected'    THEN 1 END), 0) AS rejected,
  COALESCE(SUM(CASE WHEN d.status = 'Closed'      THEN 1 END), 0) AS closed
FROM departments dept
LEFT JOIN documents d
       ON d.current_department_id = dept.department_id
      AND d.archived = FALSE
GROUP BY dept.department_id, dept.department_name$$

-- ---------------------------------------------------------------------------
-- TRIGGER 1: registration audit row
--   A newly inserted document always produces exactly one "Document registered"
--   tracking row, using the submitter as the actor.
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_documents_after_insert
AFTER INSERT ON documents
FOR EACH ROW
BEGIN
  INSERT INTO document_tracking
    (document_id, action, from_department_id, to_department_id,
     from_user_id, to_user_id, actor_id, remarks)
  VALUES
    (NEW.document_id, 'Document registered', NULL, NEW.current_department_id,
     NULL, NEW.assigned_to, NEW.submitted_by,
     COALESCE(@tracking_remarks, 'Document registered in the system'));
END$$

-- ---------------------------------------------------------------------------
-- TRIGGER 2: status change / archive audit row
--   Fires only when the status really changes (or the document is archived),
--   so ordinary edits such as a title fix do not create audit noise.
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_documents_after_update
AFTER UPDATE ON documents
FOR EACH ROW
BEGIN
  IF NEW.status <> OLD.status
     OR (NEW.archived = 1 AND OLD.archived = 0) THEN
    INSERT INTO document_tracking
      (document_id, action, from_department_id, to_department_id,
       from_user_id, to_user_id, actor_id, remarks)
    VALUES
      (NEW.document_id,
       COALESCE(@tracking_action, CONCAT('Status changed to ', NEW.status)),
       OLD.current_department_id, NEW.current_department_id,
       OLD.assigned_to, NEW.assigned_to,
       COALESCE(@tracking_actor_id, NEW.submitted_by),
       @tracking_remarks);
  END IF;
END$$

-- ---------------------------------------------------------------------------
-- PROCEDURE 1: sp_register_document
--   Registers a document and generates DOC-YYYY-#### from the new id.
--   The registration tracking row is created by trg_documents_after_insert.
-- ---------------------------------------------------------------------------
CREATE PROCEDURE sp_register_document(
  IN  p_title            VARCHAR(200),
  IN  p_description      TEXT,
  IN  p_document_type_id INT,
  IN  p_submitted_by     INT,
  IN  p_department_id    INT,
  IN  p_assigned_to      INT,
  IN  p_submission_date  DATE,
  IN  p_due_date         DATE,
  IN  p_priority         VARCHAR(10),
  OUT p_document_id      INT
)
BEGIN
  DECLARE v_user_active TINYINT DEFAULT 0;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    ROLLBACK;
    RESIGNAL;
  END;

  IF p_title IS NULL OR TRIM(p_title) = '' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Document title is required.';
  END IF;
  IF p_submission_date IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Submission date is required.';
  END IF;
  IF p_due_date IS NOT NULL AND p_due_date < p_submission_date THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Due date cannot be before the submission date.';
  END IF;
  IF p_priority NOT IN ('Low', 'Normal', 'High', 'Urgent') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid priority.';
  END IF;

  SELECT COUNT(*) INTO v_user_active
    FROM users
   WHERE user_id = p_submitted_by AND is_active = TRUE;
  IF v_user_active = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Submitting user does not exist or is inactive.';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM document_types WHERE document_type_id = p_document_type_id) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown document type.';
  END IF;

  START TRANSACTION;

  -- Temporary unique number, replaced by the readable one once the id exists.
  INSERT INTO documents
    (document_number, title, description, document_type_id, submitted_by,
     current_department_id, assigned_to, submission_date, due_date, priority, status)
  VALUES
    (CONCAT('TMP-', UUID()), p_title, p_description, p_document_type_id, p_submitted_by,
     p_department_id, p_assigned_to, p_submission_date, p_due_date,
     p_priority, 'Submitted');

  SET p_document_id = LAST_INSERT_ID();

  UPDATE documents
     SET document_number = CONCAT('DOC-', YEAR(p_submission_date), '-', LPAD(p_document_id, 4, '0'))
   WHERE document_id = p_document_id;

  COMMIT;
END$$

-- ---------------------------------------------------------------------------
-- PROCEDURE 2: sp_forward_document
--   Transactional forward: locks the row, validates, updates the owner and
--   status, and lets trg_documents_after_update write the audit row.
-- ---------------------------------------------------------------------------
CREATE PROCEDURE sp_forward_document(
  IN p_document_id      INT,
  IN p_to_department_id INT,
  IN p_to_user_id       INT,
  IN p_actor_id         INT,
  IN p_remarks          TEXT
)
BEGIN
  DECLARE v_status    VARCHAR(20);
  DECLARE v_archived  TINYINT;
  DECLARE v_user_dept INT;
  DECLARE v_user_ok   TINYINT DEFAULT 0;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    ROLLBACK;
    RESIGNAL;
  END;

  START TRANSACTION;

  SELECT status, archived INTO v_status, v_archived
    FROM documents
   WHERE document_id = p_document_id
   FOR UPDATE;

  IF v_status IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Document not found.';
  END IF;
  IF v_archived = 1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Archived documents cannot be forwarded.';
  END IF;
  IF v_status IN ('Closed', 'Approved', 'Pending') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Document cannot be forwarded in its current status.';
  END IF;

  IF p_to_user_id IS NOT NULL THEN
    SELECT is_active, department_id INTO v_user_ok, v_user_dept
      FROM users WHERE user_id = p_to_user_id;
    IF v_user_ok = 0 THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Target user is inactive or does not exist.';
    END IF;
  END IF;

  SET @tracking_action    = 'Document forwarded';
  SET @tracking_actor_id  = p_actor_id;
  SET @tracking_remarks   = p_remarks;

  UPDATE documents
     SET assigned_to           = p_to_user_id,
         current_department_id = COALESCE(p_to_department_id, v_user_dept, current_department_id),
         status                = 'Forwarded'
   WHERE document_id = p_document_id;

  SET @tracking_action = NULL, @tracking_actor_id = NULL, @tracking_remarks = NULL;

  COMMIT;
END$$

DELIMITER ;
