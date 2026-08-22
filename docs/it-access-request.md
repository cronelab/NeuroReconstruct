# Draft email to JH Research IT — access + two configuration changes

Findings from `scripts/azure_discover.ps1`, run 2026-08-22. Send as-is or trim.

---

**Subject:** NeuroReconstruct deployment — RBAC scope + container configuration

Hi,

Thanks for provisioning the resources in `JH-RIT-CRONE-App-RG`. I've started the
deployment and hit four things that need your side.

### 1. My access appears to be scoped to the App Service only

I can read and configure `rit3845-neurorecon-APP`, but every other resource in
the group returns `AuthorizationFailed` for my account
(`jkim605@jh.edu`, object id `c0ab54e8-4505-4c2b-8af3-54c8b8201217`):

| Action denied | Resource |
|---|---|
| `Microsoft.Web/serverfarms/read` | `RIT-3845-neurorecon-ASP` |
| `Microsoft.Sql/servers/databases/read` | `rit3845neuroreconsql01` |
| `Microsoft.Storage/storageAccounts/fileServices/shares/read` | `rit3845neuroreconst01` |
| `Microsoft.ContainerRegistry/registries/read` (and push — 401) | `rit3845neuroreconacr01` |

Simplest fix would be **Contributor on the resource group** for `jkim605` and
`ncrone1`. If you'd rather stay least-privilege, the minimum we need is:

| Scope | Role | Needed for |
|---|---|---|
| Resource group | Reader | seeing the resources at all |
| ACR `rit3845neuroreconacr01` | **AcrPush** | pushing the application image |
| Storage `rit3845neuroreconst01` | Storage Account Contributor + **Storage File Data SMB Share Contributor** | creating the share and uploading imaging data |
| SQL server `rit3845neuroreconsql01` | Entra admin *(or you run the SQL in §4)* | creating the app's database user |
| App Service | Website Contributor | app settings, container config |

### 2. The App Service is configured for code, not containers

It currently reports `LinuxFxVersion = PYTHON|3.12`, i.e. the built-in Python
runtime with an Oryx build.

Our application can't run that way. It depends on ANTs, TensorFlow, Open3D and
**ODBC Driver 18**, which need system packages (`libgl1`, `libgomp1`,
`msodbcsql18`) installed as root — not possible during an Oryx build. It also
needs ~1 GB of pretrained model weights baked in at build time.

I assume the container registry was provisioned for exactly this. Could you
switch the app to **pull a container image** from
`rit3845neuroreconacr01.azurecr.io`? We'll publish `neurorecon:1.0`.

Please also grant the app's **system-assigned managed identity**
(principal `533de2af-bf50-4c80-b78e-a22e6a087037`) the **AcrPull** role on the
registry so it can pull the image.

**Related question:** `WEBSITES_VNET_ROUTE_ALL` is set, so outbound traffic is
routed through the VNet. Will the App Service be able to reach the registry —
does the ACR have a private endpoint, or does the VNet have outbound access? If
the pull path isn't open, the container won't start.

### 3. The Azure Files share isn't mounted

`WEBSITES_ENABLE_APP_SERVICE_STORAGE` and `NEURO_DATA_DIR` are both set, but
`az webapp config storage-account list` returns empty — no share is attached.

Could you mount the 256 GiB share to the App Service at **`/mounts/neurodata`**?
That path is where all imaging files live (~5 GB initially, growing ~350 MB per
patient). The app reads its data root from `NEURO_DATA_DIR`, so the two need to
agree.

### 4. Values we need, and one bit of SQL

You pre-created `AZURE_SQL_SERVER`, `AZURE_SQL_DATABASE`,
`AZURE_SQL_AUTHENTICATION` and `NEURO_DATA_DIR`. Could you send me the values —
particularly the **database name** and the intended authentication mode? (I can
read the names but not the values at my current access level.)

Assuming the app authenticates as its managed identity, someone with Entra admin
on the SQL server needs to run this once against the database:

```sql
CREATE USER [rit3845-neurorecon-APP] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [rit3845-neurorecon-APP];
ALTER ROLE db_datawriter ADD MEMBER [rit3845-neurorecon-APP];
ALTER ROLE db_ddladmin ADD MEMBER [rit3845-neurorecon-APP];
```

`db_ddladmin` is needed because the application creates and migrates its own
schema (four small metadata tables — all imaging data is on the file share, not
in SQL).

Happy to run it myself if you'd rather make me Entra admin on the server.

Thanks,
Jeongjun
