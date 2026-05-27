SET NOCOUNT ON;

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'analytics')
    EXEC(N'CREATE SCHEMA [analytics]');

DROP VIEW IF EXISTS analytics.vw_PersonDetail;
GO
CREATE VIEW analytics.vw_PersonDetail
AS
SELECT
    a.agent_id,
    a.full_name,
    a.employee_id,
    t.name AS team_name,
    s.name AS site_name,
    a.hire_date,
    a.status,
    a.bu_id
FROM wfm.agent AS a
INNER JOIN wfm.team AS t
    ON a.team_id = t.team_id
    AND a.bu_id = t.bu_id
INNER JOIN wfm.site AS s
    ON a.site_id = s.site_id
    AND a.bu_id = s.bu_id;
GO

DROP VIEW IF EXISTS analytics.vw_AbsenceRequest;
GO
CREATE VIEW analytics.vw_AbsenceRequest
AS
SELECT
    ar.request_id,
    ar.agent_id,
    a.full_name AS agent_name,
    atp.name AS absence_type_name,
    ar.start_date,
    ar.end_date,
    ar.status,
    DATEDIFF(DAY, ar.start_date, ar.end_date) + 1 AS [days],
    ar.bu_id
FROM absence.absence_request AS ar
INNER JOIN wfm.agent AS a
    ON ar.agent_id = a.agent_id
    AND ar.bu_id = a.bu_id
INNER JOIN absence.absence_type AS atp
    ON ar.absence_type_id = atp.absence_type_id
    AND ar.bu_id = atp.bu_id;
GO

DROP VIEW IF EXISTS analytics.vw_OvertimeRequest;
GO
CREATE VIEW analytics.vw_OvertimeRequest
AS
SELECT
    ot.ot_id,
    ot.agent_id,
    a.full_name AS agent_name,
    ot.[date],
    ot.hours,
    ot.status,
    ot.reason,
    ot.bu_id
FROM overtime.overtime_request AS ot
INNER JOIN wfm.agent AS a
    ON ot.agent_id = a.agent_id
    AND ot.bu_id = a.bu_id;
GO

DROP VIEW IF EXISTS analytics.vw_Scheduling;
GO
CREATE VIEW analytics.vw_Scheduling
AS
SELECT
    sh.shift_id,
    sh.agent_id,
    a.full_name AS agent_name,
    t.name AS team_name,
    sh.[date],
    sh.shift_name,
    sh.start_time,
    sh.end_time,
    sh.queue_name,
    sh.bu_id
FROM scheduling.shift AS sh
INNER JOIN wfm.agent AS a
    ON sh.agent_id = a.agent_id
    AND sh.bu_id = a.bu_id
INNER JOIN wfm.team AS t
    ON a.team_id = t.team_id
    AND a.bu_id = t.bu_id;
GO
