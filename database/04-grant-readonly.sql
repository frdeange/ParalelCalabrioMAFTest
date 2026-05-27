SET NOCOUNT ON;

IF NOT EXISTS (
    SELECT 1
    FROM sys.database_principals
    WHERE name = N'calabriomaf-uais'
)
BEGIN
    CREATE USER [calabriomaf-uais] FROM EXTERNAL PROVIDER;
END;

IF EXISTS (
    SELECT 1
    FROM sys.database_role_members drm
    INNER JOIN sys.database_principals role_principal
        ON drm.role_principal_id = role_principal.principal_id
    INNER JOIN sys.database_principals member_principal
        ON drm.member_principal_id = member_principal.principal_id
    WHERE role_principal.name = N'db_datawriter'
      AND member_principal.name = N'calabriomaf-uais'
)
BEGIN
    ALTER ROLE [db_datawriter] DROP MEMBER [calabriomaf-uais];
END;

IF EXISTS (
    SELECT 1
    FROM sys.database_role_members drm
    INNER JOIN sys.database_principals role_principal
        ON drm.role_principal_id = role_principal.principal_id
    INNER JOIN sys.database_principals member_principal
        ON drm.member_principal_id = member_principal.principal_id
    WHERE role_principal.name = N'db_datareader'
      AND member_principal.name = N'calabriomaf-uais'
)
BEGIN
    ALTER ROLE [db_datareader] DROP MEMBER [calabriomaf-uais];
END;

IF NOT EXISTS (
    SELECT 1
    FROM sys.database_principals
    WHERE name = N'uai_readonly'
      AND type = N'R'
)
BEGIN
    CREATE ROLE [uai_readonly];
END;

REVOKE SELECT ON SCHEMA::[_metadata] FROM [calabriomaf-uais];
REVOKE SELECT ON SCHEMA::[analytics] FROM [calabriomaf-uais];
GRANT SELECT ON SCHEMA::[_metadata] TO [uai_readonly];
GRANT SELECT ON SCHEMA::[analytics] TO [uai_readonly];

IF NOT EXISTS (
    SELECT 1
    FROM sys.database_role_members drm
    INNER JOIN sys.database_principals role_principal
        ON drm.role_principal_id = role_principal.principal_id
    INNER JOIN sys.database_principals member_principal
        ON drm.member_principal_id = member_principal.principal_id
    WHERE role_principal.name = N'uai_readonly'
      AND member_principal.name = N'calabriomaf-uais'
)
BEGIN
    ALTER ROLE [uai_readonly] ADD MEMBER [calabriomaf-uais];
END;

SELECT
    user_name = dp.name,
    role_name = rp.name
FROM sys.database_role_members drm
INNER JOIN sys.database_principals rp
    ON drm.role_principal_id = rp.principal_id
INNER JOIN sys.database_principals dp
    ON drm.member_principal_id = dp.principal_id
WHERE dp.name = N'calabriomaf-uais'
ORDER BY rp.name;

SELECT
    schema_name = s.name,
    permission_name = p.permission_name,
    state_desc = p.state_desc,
    grantee = dp.name
FROM sys.database_permissions AS p
INNER JOIN sys.schemas AS s
    ON p.major_id = s.schema_id
INNER JOIN sys.database_principals AS dp
    ON p.grantee_principal_id = dp.principal_id
WHERE dp.name = N'uai_readonly'
  AND s.name IN (N'analytics', N'_metadata')
ORDER BY s.name, p.permission_name;
