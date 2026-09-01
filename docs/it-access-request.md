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

---

## Follow-up message — Entra ID sign-in

Send as a reply on the same thread. Independent of the four items above; the
deployment can proceed either way.

**Subject:** re: NeuroReconstruct — Entra ID sign-in for the app?

One more question, on authentication rather than infrastructure.

The application currently manages its own accounts: a `users` table with bcrypt
password hashes, a JWT issued at login, and three roles (viewer / editor / admin).
That was fine as a single-user desktop tool, but for a hosted multi-user app it
means we own password storage, resets and account lifecycle ourselves -- and today
there is no password-reset flow at all.

Since the app is Hopkins-internal and the App Service already has a managed
identity, would you recommend putting **Entra ID authentication (App Service "Easy
Auth")** in front of it instead? We would get JHED single sign-on, access managed
by an Entra group rather than a table in our database, and no password handling on
our side.

Specifically:

1. Is Easy Auth / Entra the standard for research apps in this environment?
2. If so, can you enable it on `rit3845-neurorecon-APP` and point it at a group we
   nominate?
3. Would you expect us to keep our internal role model (mapping the Entra identity
   onto viewer/editor/admin on first sign-in), or drive roles from Entra app roles?

If Entra is not straightforward here, we will stay on the built-in login -- it
works, and the app is network-restricted to Hopkins already. This is about reducing
what we have to maintain, not a blocker.

Thanks,
Jeongjun


---

## Reply #2 — 2026-08-28, after IT restored permissions

Verified with `scripts/azure_discover.ps1`. Most of the earlier asks are now
done; one permission gap blocks the image push.

**Subject:** re: NeuroReconstruct — permissions confirmed, one gap left on the registry

Thanks — that did it. I can now read the plan, SQL server, storage account and
registry, and most of what I asked for is in place:

- App Service is in **container mode** (`DOCKER|.../quickstart:1.0`) and set to
  pull with its **managed identity**, which has **AcrPull**. 
- SQL database **`neurorecondb01`** is there, `ActiveDirectoryMsi`. 
- The **`neurodata` share is mounted** at `/mounts/neurodata`. 

### No, I haven't been able to push the container yet

`az acr build` gets as far as uploading the build context and then fails:

```
ERROR: Failed to get a SAS URL to upload context.
(AuthorizationFailed) The client 'jkim605@jh.edu' does not have authorization to
perform action 'Microsoft.ContainerRegistry/registries/listBuildSourceUploadUrl/action'
over scope '.../registries/rit3845neuroreconacr01'
```

The registry has **AcrPush** assigned to the `JH-RIT-CRONE` group, but AcrPush
only grants `registries/pull/read` and `registries/push/write`. A server-side
build additionally needs the ACR Tasks actions, which AcrPush does not include:

| Action |
|---|
| `Microsoft.ContainerRegistry/registries/listBuildSourceUploadUrl/action` |
| `Microsoft.ContainerRegistry/registries/scheduleRun/action` |
| `Microsoft.ContainerRegistry/registries/runs/read` |
| `Microsoft.ContainerRegistry/registries/runs/listLogSasUrl/action` |

**Simplest fix:** `Contributor` on `rit3845neuroreconacr01` for `jkim605` (and
`ncrone1`). A custom role with just those four actions on top of AcrPush works
equally well if you prefer least privilege.

Could you also confirm I'm actually a **member of `JH-RIT-CRONE`**? That's where
the AcrPush assignment lives, and I can't see my own membership of it.

**Why a server-side build rather than pushing from my laptop:** the image is
large (TensorFlow, ANTs, the ODBC driver, and ~1 GB of pretrained model weights
baked in — the app can't download them at runtime because outbound traffic is
VNet-routed). Building in ACR means uploading a **~2 MB source context** instead
of pushing ~10 GB over the VPN. I can build locally instead if Contributor is a
problem, but that needs Docker Desktop installed on my workstation.

### On the file share

From my side the configuration looks correct: the share is attached, the app is
VNet-integrated, `WEBSITE_CONTENTOVERVNET` and `WEBSITES_VNET_ROUTE_ALL` are set,
and `rit3845neuroreconst01.file.core.windows.net` resolves to a private address.

The mount shows `state: NotValidated`. I believe that's expected rather than
broken — the storage account denies public access, so App Service's validation
probe can't reach it from outside the VNet even when the runtime mount over the
VNet will succeed. The real test is a container that starts and can list the
path, which I can run as soon as I can push an image.

If it does turn out to be genuinely broken, the usual culprit is **outbound TCP
445** from the App Service integration subnet
(`AZ-EAST-...-10.208.209.128-28-AppSvcIntegration`) — SMB needs it, and some NSG
baselines block it. That subnet is in `INFRASTRUCTURE-SVI-USE-ONLY-RG`, which I
can't read.

Thanks,
Jeongjun

---

### Discovery snapshot, 2026-08-28

| Item | State |
|---|---|
| App Service kind | `app,linux,container` |
| Image | `DOCKER|rit3845neuroreconacr01.azurecr.io/quickstart:1.0` (IT placeholder) |
| `acrUseManagedIdentityCreds` | `true` |
| Managed identity `533de2af-…` | **AcrPull** on the registry |
| SQL database | `neurorecondb01`, `GP_S_Gen5_2`, **Paused** (serverless auto-pause) |
| SQL auth | `ActiveDirectoryMsi` |
| Storage mount | `neurodata` → `/mounts/neurodata`, SMB, `NotValidated` |
| ACR | Premium, `publicNetworkAccess: Disabled`, private endpoint approved, admin disabled |
| ACR from workstation | reachable — resolves to `10.208.209.5`, data-plane reads work |
| Blocker | `listBuildSourceUploadUrl` denied — needs Contributor on the ACR |

App settings IT created, which the app now reads directly (see
`backend/database.py::_azure_sql_url`): `AZURE_SQL_SERVER`, `AZURE_SQL_DATABASE`,
`AZURE_SQL_AUTHENTICATION`, `DATABASE_AUTH_MODE`, `DATABASE_NAME`,
`NEURO_DATA_DIR=/mounts/neurodata`, `WEBSITES_PORT=80`.

> **Note:** `az webapp config storage-account list` returns the storage account
> access key in plain text. `scripts/azure_discover.ps1` now projects it away so
> its output is safe to paste into email.


---

## Reply #3 — 2026-08-31, registry firewall blocks the build agent

The Contributor grant worked. A second, independent blocker surfaced behind it.

**Subject:** re: NeuroReconstruct — permissions fixed, now the registry firewall blocks the build agent

The permission change worked — the build now uploads its context, queues, and
gets an agent. It then fails at the point the agent tries to log in to the
registry:

```
Logging in to registry: rit3845neuroreconacr01.azurecr.io
failed to login: Get "https://rit3845neuroreconacr01.azurecr.io/v2/":
denied: client with IP '57.151.4.106' is not allowed access.
Run ID: ca1 failed after 6s.
```

The registry has `publicNetworkAccess: Disabled`. ACR Tasks runs on Azure-hosted
build agents that reach the registry over its **public** endpoint, so they're
refused. The `AzureServices` network bypass doesn't cover Tasks agents, and an IP
allow-rule won't help either — that `57.151.4.106` is assigned per run from a
shared pool, so it's different every time.

I can't fix this from my side: I don't have rights to change the registry's
network configuration, and the VNet lives in `INFRASTRUCTURE-SVI-USE-ONLY-RG`.

### Two ways forward — your call

**Option 1 — temporarily allow public network access (fastest).**
Set the registry's public network access to `Enabled` for about an hour while I
run the build, then set it back to `Disabled`. I'll tell you the moment it
finishes.

To be clear about what this does and doesn't expose: the registry still requires
Entra authentication throughout. Enabling public access makes the *endpoint*
reachable from the internet; it does not make the registry anonymous or public.
The image pull path is unaffected either way — App Service pulls over the private
endpoint with its managed identity.

**Option 2 — a dedicated ACR agent pool inside the VNet (durable).**
This is the supported way to build against a network-restricted registry. The
registry is already **Premium**, which supports it. It needs a delegated subnet
in the VNet:

```
az acr agentpool create --registry rit3845neuroreconacr01     --name neuroreconpool --tier S2 --subnet-id <subnet-resource-id>
```

Then builds run with `--agent-pool neuroreconpool` and never touch the public
endpoint. This is the better answer if we expect to rebuild the image regularly,
at the cost of a subnet and the agent pool's own charge. (The CLI still flags
`az acr agentpool` as preview.)

I'd suggest **Option 1 to get the app deployed now**, and Option 2 later if
rebuilds become routine — but I'm happy to go straight to Option 2 if you'd
rather not open the endpoint at all.

**A third option, which I think is worse:** I build the image locally and push it
over the private endpoint, which works with the network exactly as it is. But
Docker Desktop isn't installed on my workstation and I'm not a local admin, so
that needs your help regardless — and it would mean pushing ~10 GB over the VPN
instead of uploading a 500 KB source context. Only worth it if both options above
are unacceptable.

Thanks,
Jeongjun

---

### Correction to Reply #2

Reply #2 said the problem was "not a network problem, purely RBAC." That was
right about the RBAC gap being real, but wrong to rule out the network: the
firewall blocker was simply hidden behind the permission failure and only
surfaced once the build got far enough to attempt a registry login. Both were
genuine, and they are independent.

The evidence that misled me — the registry resolving to `10.208.209.5` and
data-plane reads succeeding from the workstation — was about the **workstation's**
path to the registry, which goes through the private endpoint. It said nothing
about the **build agent's** path, which does not.


---

## Reply #4 — 2026-09-01, agent pool is memory-starved

The private agent pool works. Builds now run end to end and fail only at the last
step, on memory. Measured, not guessed.

**Subject:** re: NeuroReconstruct — private pool works; it was created S1 rather than S2

The agent pool did the job — builds now reach the registry fine and get all the
way through the frontend build, the system packages, the ODBC driver, and the
full Python install (TensorFlow, ANTs, Open3D). Thank you.

They now fail at the final step, which pre-caches the pretrained neuroimaging
models into the image. That step runs the models once, and the S1 agent doesn't
have the memory for it. I measured it in isolation on the pool:

| Measurement | Value |
|---|---|
| Agent memory (`MemTotal`) | 2,993,724 kB (**2.86 GB**) |
| Brain extraction, alone — peak RSS | 2,473,712 kB (2.36 GB) — **succeeds** |
| DKT parcellation, alone — peak RSS | 2,785,492 kB (2.66 GB) — **exit 137, OOM-killed** |

Exit 137 is the kernel's OOM killer. The second figure is a single model in a
freshly started process, so I can't work around it by splitting the step up — one
model on its own already exceeds what the agent has.

The pool came through as **S1**; my earlier message had specified **S2** in the
`az acr agentpool create` line, so I think the tier just got lost along the way
rather than being a deliberate choice. (If it *was* deliberate — cost, subnet
sizing, or anything else — just say so and I'll take the low-memory route below
instead.)

**Could you set `rit3845nracrpool01` to S2** (4 vCPU / 8 GB)? That clears the
ceiling with plenty of headroom, and the extra cores should roughly halve build
time as a side effect. Nothing else about the pool needs to change.

I'd have adjusted it myself, but the pool is tagged `IaC: Terraform` and I didn't
want to cause drift from your state file.

**If you'd rather not resize:** I can rewrite that step to download the model
weights without running the models, which needs very little memory. I'm proposing
the resize first because running them is what guarantees the *right* files get
cached — and the app can't fetch them at runtime, since outbound traffic is
VNet-routed.

For reference, the build history: `ca1` failed on the registry firewall (before
the pool existed), `ca2`/`ca6` are the real builds that died at this warm-up step,
and `ca4`/`ca5`/`ca7` are diagnostic runs that succeeded and produced the numbers
above. `ca8` is the OOM measurement.

Thanks,
Jeongjun
