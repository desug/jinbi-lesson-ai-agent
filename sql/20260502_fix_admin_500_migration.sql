-- Jinbi Lesson admin 500 fix migration
-- 目的：补齐管理员新增学生、年级统计、加/扣课所需字段。
-- 说明：数据库列沿用现有 SQLAlchemy snake_case 命名，API 通过 Pydantic 输出 camelCase。
-- 使用：先 USE jinbi_backend_db; 再执行本文件。

USE jinbi_backend_db;

-- students.grade：新增学生默认归入“未分配”。
SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'students' AND COLUMN_NAME = 'grade') = 0,
  'ALTER TABLE students ADD COLUMN grade VARCHAR(20) NOT NULL DEFAULT ''未分配''',
  'ALTER TABLE students MODIFY COLUMN grade VARCHAR(20) NOT NULL DEFAULT ''未分配'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- students.class_type：项目现有代码使用 vip/small；前端展示为 VIP/小班。
SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'students' AND COLUMN_NAME = 'class_type') = 0,
  'ALTER TABLE students ADD COLUMN class_type VARCHAR(20) NOT NULL DEFAULT ''vip''',
  'ALTER TABLE students MODIFY COLUMN class_type VARCHAR(20) NOT NULL DEFAULT ''vip'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- students.isDeleted / deletedAt：软删除，列表、统计、AI 查询过滤该字段。
SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'students' AND COLUMN_NAME = 'isDeleted') = 0,
  'ALTER TABLE students ADD COLUMN isDeleted TINYINT(1) NOT NULL DEFAULT 0',
  'SELECT ''students.isDeleted exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'students' AND COLUMN_NAME = 'deletedAt') = 0,
  'ALTER TABLE students ADD COLUMN deletedAt DATETIME NULL',
  'SELECT ''students.deletedAt exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'students' AND INDEX_NAME = 'ix_students_isDeleted') = 0,
  'CREATE INDEX ix_students_isDeleted ON students (isDeleted)',
  'SELECT ''ix_students_isDeleted exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- lesson_records.subject_name / change_type / hours：课时流水核心字段。
SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'subject_name') = 0,
  'ALTER TABLE lesson_records ADD COLUMN subject_name VARCHAR(100) NOT NULL DEFAULT ''综合''',
  'SELECT ''lesson_records.subject_name exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'change_type') = 0,
  'ALTER TABLE lesson_records ADD COLUMN change_type VARCHAR(20) NOT NULL DEFAULT ''add''',
  'SELECT ''lesson_records.change_type exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'hours') = 0,
  'ALTER TABLE lesson_records ADD COLUMN hours DECIMAL(10,2) NOT NULL DEFAULT 0.00',
  'SELECT ''lesson_records.hours exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- lesson_records.amount：加课金额；扣课默认 0。
SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'amount') = 0,
  'ALTER TABLE lesson_records ADD COLUMN amount DECIMAL(10,2) NOT NULL DEFAULT 0.00',
  'SELECT ''lesson_records.amount exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- lesson_records.remaining_hours：记录每次变动后的剩余课时，便于流水展示。
SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'remaining_hours') = 0,
  'ALTER TABLE lesson_records ADD COLUMN remaining_hours DECIMAL(10,2) NULL',
  'SELECT ''lesson_records.remaining_hours exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- lesson_records.recordDate：业务记录日期；旧数据用 created_at 回填。
SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'recordDate') = 0,
  'ALTER TABLE lesson_records ADD COLUMN recordDate DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP',
  'SELECT ''lesson_records.recordDate exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'operator_name') = 0,
  'ALTER TABLE lesson_records ADD COLUMN operator_name VARCHAR(100) NOT NULL DEFAULT ''系统''',
  'SELECT ''lesson_records.operator_name exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'remark') = 0,
  'ALTER TABLE lesson_records ADD COLUMN remark TEXT NULL',
  'SELECT ''lesson_records.remark exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'created_at') = 0,
  'ALTER TABLE lesson_records ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP',
  'SELECT ''lesson_records.created_at exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 兼容旧字段：如果历史库使用 camelCase 或 type/action/changeHours/remainHours，则回填到当前 SQLAlchemy 字段。
SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'subjectName') > 0,
  'UPDATE lesson_records SET subject_name = COALESCE(NULLIF(subject_name, ''''), subjectName, ''综合'')',
  'SELECT ''lesson_records.subjectName not found'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'type') > 0,
  'UPDATE lesson_records SET change_type = CASE WHEN `type` IN (''add'', ''增加'', ''加课时'') THEN ''add'' ELSE ''deduct'' END WHERE `type` IS NOT NULL AND `type` <> ''''',
  'SELECT ''lesson_records.type not found'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'action') > 0,
  'UPDATE lesson_records SET change_type = CASE WHEN action IN (''add'', ''增加'', ''加课时'') THEN ''add'' ELSE ''deduct'' END WHERE action IS NOT NULL AND action <> ''''',
  'SELECT ''lesson_records.action not found'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'changeHours') > 0,
  'UPDATE lesson_records SET hours = changeHours WHERE changeHours IS NOT NULL AND (hours IS NULL OR hours = 0)',
  'SELECT ''lesson_records.changeHours not found'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'remainingHours') > 0,
  'UPDATE lesson_records SET remaining_hours = COALESCE(remaining_hours, remainingHours) WHERE remainingHours IS NOT NULL',
  'SELECT ''lesson_records.remainingHours not found'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lesson_records' AND COLUMN_NAME = 'remainHours') > 0,
  'UPDATE lesson_records SET remaining_hours = COALESCE(remaining_hours, remainHours) WHERE remainHours IS NOT NULL',
  'SELECT ''lesson_records.remainHours not found'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE lesson_records
SET recordDate = COALESCE(recordDate, created_at, NOW())
WHERE recordDate IS NULL;

-- payments：如果表不存在则创建；如果已存在则补齐关键字段。
CREATE TABLE IF NOT EXISTS payments (
  id INT NOT NULL AUTO_INCREMENT,
  student_id INT NOT NULL,
  amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  pay_type VARCHAR(50) NOT NULL DEFAULT 'lesson_add',
  remark TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  INDEX ix_payments_student_id (student_id),
  INDEX ix_payments_pay_type (pay_type),
  INDEX ix_payments_student_id_created_at (student_id, created_at),
  CONSTRAINT fk_payments_student_id_students
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payments' AND COLUMN_NAME = 'amount') = 0,
  'ALTER TABLE payments ADD COLUMN amount DECIMAL(10,2) NOT NULL DEFAULT 0.00',
  'SELECT ''payments.amount exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payments' AND COLUMN_NAME = 'student_id') = 0,
  'ALTER TABLE payments ADD COLUMN student_id INT NOT NULL',
  'SELECT ''payments.student_id exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payments' AND COLUMN_NAME = 'remark') = 0,
  'ALTER TABLE payments ADD COLUMN remark TEXT NULL',
  'SELECT ''payments.remark exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payments' AND COLUMN_NAME = 'created_at') = 0,
  'ALTER TABLE payments ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP',
  'SELECT ''payments.created_at exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
