// The in-app copy of the user guide, shown by UserGuideModal.
//
// The source is the shared "User Guide" doc; this is a snapshot of it, edited
// where the app now behaves differently.
// When the doc changes, paste its Markdown export here (drop the byline, keep
// {{SCREEN_MAP}} where the screen diagram goes) and bump GUIDE_VERSION so
// everyone sees the new version once on their next sign-in.
//
// The renderer (Markdown.jsx) reads a small subset: #/##/### headings,
// paragraphs, - / 1. / - [ ] lists (nested by 2+ spaces), > quotes, pipe
// tables, **bold**, `code`, [text](url) and <url> links.

export const GUIDE_VERSION = 1;

export const GUIDE_TITLE = 'User Guide';

export const GUIDE_MARKDOWN = `
## What NeuroReconstruct is

NeuroReconstruct is a web app that shows where every implanted sEEG or ECoG contact sits in a patient's brain. It lines up the post-implant CT, where the metal contacts show, with the patient's MRI, where the anatomy shows. It then builds a 3D brain you can turn around and names the brain structure each contact sits in.

You will mostly use it to:

- Look at a patient's implant in 3D and on MRI slices.
- Find out which structure a contact is in (for example, the left hippocampus).
- Try out recorded sEEG activity painted onto the brain. This feature is still experimental (beta).

Patients appear by research ID (for example PY26N010), never by name. Region names are computed automatically from each patient's own MRI. Treat them as a strong guide, and check the images yourself when a contact sits near a boundary.

## Before you start

You need four things. Tick them off before your first login.

- [ ] **A Hopkins network connection.** The app only opens from inside the Hopkins network: a hospital computer, or your own laptop connected to the Hopkins VPN.
- [ ] **Google Chrome or Microsoft Edge, up to date.** The 3D brain needs a modern browser with 3D graphics turned on.
- [ ] **A mouse with a scroll wheel.** A trackpad works, but rotating, moving and zooming the brain is much easier with a mouse.
- [ ] **The web address and the shared login.**
  - Web address: <https://rit3845-neurorecon-app.azurewebsites.net> (bookmark it).
  - Username: \`viewer\`
  - Password: you get it from JJ Kim (<jkim605@jhmi.edu>). It is left out of this guide on purpose, so the guide can be passed around safely.

The \`viewer\` login is shared by everyone on the team. Please do not write the password down in shared places, save it in the browser on a shared hospital computer, or give it to anyone outside the team. If you think it has leaked, tell the lab so they can change it.

## Logging in

Signing in takes under a minute.

1. Open Chrome or Edge and go to the web address above.
2. You will see a dark page titled **NeuroReconstruct — sEEG / ECoG Brain Viewer** with two boxes.
3. In **USERNAME**, type \`viewer\` (all lower case).
4. In **PASSWORD**, type the password you were given.
5. Click **Sign In**.
6. You land on a page called **Electrode Reconstructions**. In the top-right corner you should see a small grey tag reading **viewer** next to the username. That confirms you are in.

If you see **Invalid username or password**, retype both slowly. Check that Caps Lock is off and that no space crept in before or after. If it still fails, the password may have been changed; ask the lab.

**Signing out.** Click the power-button symbol (a circle with a line through the top) at the far top-right, next to the username. Always sign out on a shared computer.

**Staying signed in.** A sign-in lasts 8 hours. After that, images and brains simply stop loading, with no warning message. Press F5 to reload the page and you will be asked to sign in again.

**Share links.** Someone may send you a link that contains \`/view/\` followed by a number. It opens one patient directly, with no login.

**This guide.** It opens by itself the first time you sign in on a computer. Open it again any time with the **? Guide** button in the top bar.

## What a viewer account can and cannot do

The \`viewer\` login is read-only: you can look at everything, but nothing you do changes a patient's data. Settings you change on screen, such as transparency or which structures are shown, affect only your own screen and are not saved.

| Task | Viewer (you) | Notes |
| --- | --- | --- |
| Browse the list of patients | Yes | You see every patient in the system, not only your own. |
| Rotate, zoom and move the 3D brain | Yes | |
| Show or hide brain structures, change transparency | Yes | Only your screen changes. |
| Point at a contact to see its brain region | Yes | |
| Scroll through MRI slices, compare T2 / FLAIR / FA side by side | Yes | Extra scans appear only if the lab added them. |
| Look at the Fusion view (CT laid over MRI) | Yes | Its **Confirm** and **Re-run precise** buttons are for editors and will not work for you. |
| Open the sEEG Activity map | Yes | Experimental (beta). Only on Completed patients that have a recording uploaded. |
| Download the MNI export | Not needed | For research only. Its button shows on some Completed patients; you can ignore it. |
| Place, move or delete electrode contacts | No | Editors only. |
| Create a patient, or upload MRI, CT or sEEG files | No | Editors only. |
| Mark a patient Complete, unlock it, run the MNI export, make share links | No | Editors only. |
| Delete or restore a patient | No | You can look at the **Deleted** list but cannot restore anything. |

**Stick to Completed patients.** A patient marked **In Progress** is still being worked on, so its contacts may be missing or not yet checked. It also opens in the editors' layout, with buttons for placing contacts. Those buttons will not save anything for you, so you cannot break it, but the screen can be confusing.

## Finding a patient

After signing in you see **Electrode Reconstructions**: one card per patient, split into two columns.

- **In Progress** (left, orange heading): still being worked on by the lab.
- **Completed** (right, green heading): finished and locked. Sorted by patient ID. Start here.

Each card shows:

- **ID** — the research ID, for example \`ID: PY26N010\`.
- **Shafts** — how many electrodes were implanted.
- **Contacts** — how many recording points have been placed in total.
- The date the reconstruction was created, and **🔒 Locked** or **✎ In Progress** at the bottom.

A card can also carry a warning. A spinning circle with **Co-registering MRI + CT...** or **Uploading MRI + CT...** means it is still processing; clicking it only shows a "please wait" message. A red **⚠ CT registration failed** means something went wrong; let the lab know.

**To find one patient,** press Ctrl+F and type the ID. There is no search box, but the browser's own Find works on this page. The list refreshes itself every 10 seconds.

**To open a patient,** click anywhere on the card.

**To get back to the list,** click the **NeuroReconstruct** logo at the top-left. Do not use the browser's Back button: it changes the address but leaves you on the same patient.

## The patient screen at a glance

{{SCREEN_MAP}}

Every patient opens in this layout. The next three sections walk through the main view and the side panel in detail.

## Exploring the 3D brain

A Completed patient opens on the 3D view: a see-through grey brain with the electrodes inside. Each shaft has its own colour, and its contacts are balls joined by a thin line. Depth (sEEG) contacts are small balls; grid and strip (ECoG) contacts are larger.

### Moving the brain

| To | With a mouse | With a trackpad |
| --- | --- | --- |
| Rotate | Hold the left button and drag | Press down and drag |
| Zoom in or out | Roll the scroll wheel | Two-finger scroll, or pinch |
| Slide the brain sideways (pan) | Hold the right button and drag | Awkward; use a mouse |
| Start over from the default view | Press F5 to reload the page | Press F5 |

The same reminders are printed along the bottom of the 3D view.

### Display panel (right side, top)

- **MRI Brain Surface** — the grey see-through brain. Its slider sets how solid it looks; it starts at 30%. Lower it, or untick it, to see deep electrodes clearly.
- **CT Electrodes** — the metal exactly as the CT shows it, drawn as a 3D shape. It appears only when the patient has a CT, and starts unticked. Its **Floor** and **Ceiling** handles set how bright on CT, in Hounsfield units (HU), something must be to be drawn. The floor starts at 2000 HU, which shows mostly metal. Use it to check that the coloured contact balls sit on the real metal.

### Brain panel (right side, middle)

1. Click **⊕ Load**. The first time can take a while; the button reads **Computing…** meanwhile.
2. Choose how the brain is drawn:
    - **Structures** — separate see-through coloured regions. Deep contacts stay visible, so this is usually the best choice for sEEG.
    - **Cortical surface** — one solid folded surface. Gyri and sulci are easy to read, but deep contacts are hidden behind it. It is coloured by the **DKT** atlas by default; the **Colour** buttons also offer **Plain** (white) and, when the lab uploaded FreeSurfer results, **Other ▸** with the Desikan-Killiany and Destrieux atlases. Point at a region to read its name.
3. In Structures mode, an **Opacity** slider and a folded **Show brain structures** list appear:
    - The box beside **Show brain structures** turns everything on or off at once; turning it back on restores your last selection.
    - Click **▸** to open the list. Structures are grouped as Subcortical, Frontal, Temporal, Parietal, Occipital and Cingulate; click a group's **▸** to see its Left and Right structures.
    - A dash (–) in a box means only some of the structures inside it are shown.
    - The same choices decide what the atlas overlay paints on the 2D slices.
4. In Cortical surface mode, with an atlas chosen under **Colour**, a folded **Region colours** list appears. Every region starts coloured. Click **▸** to open it, then untick a region (or a whole lobe) to draw it white. The 2D slices follow the same choices.
5. If the lab has uploaded FreeSurfer results, a **Source: Fast / FreeSurfer** row shows which labelling is in use. You can see it but not switch it.

### Pointing at things

Once structures are loaded, a small button appears at the top-left of the 3D view. It reads either **Hover structure → contacts** or **Hover contact → structure**; click it to switch.

- **structure → contacts**: point at a structure. The top-right corner shows its name and how many contacts are inside it, and those contacts get a coloured ring.
- **contact → structure**: point at a contact. The top-right corner shows the contact's name and the region it sits in (see Electrodes and contacts below).

The button only shows in Structures mode. Clicking a contact turns it white and keeps its name on screen; click it again to let go.

### Electrodes list (right side, bottom)

Every shaft is listed with its colour dot, short name (for example \`LA\`), full label, and the number of contacts placed (for example \`12c\`). The bottom-left of the 3D view shows the totals.

## Reading the MRI and CT slices

The narrow column on the left switches between views: **3D**, **Sagittal**, **Axial** and **Coronal** (plus **Fusion** when a CT has been lined up with the MRI). Click a small picture to make it the main view.

### Moving through slices

- Roll the scroll wheel over the image to step one slice at a time.
- Or drag the thin slider along the right edge of the image to jump.
- The top-left corner names the plane and slice number; the bottom-right reads, for example, \`scroll · 112 / 256\`.
- A small picture in a corner shows a line marking where the current slice cuts through the head.

**Left and right.** Slices follow radiological convention: the patient's right is on the left of the screen in Axial and Coronal views. In Sagittal views, the front of the head is on the left. No L or R letters are shown, so keep this in mind.

### Contacts and the atlas overlay on the slices

- Contacts within 4 mm of the slice are drawn in their shaft colour; those further from the slice fade out.
- Point at a contact to see its name and region, exactly as in the 3D view.
- The **OVERLAY** bar above the slices paints atlas regions onto them, following the Brain panel's mode:
  - **Structures** mode: every structure, subcortical and cortical. Click **⊕ Load atlas** the first time.
  - **Cortical surface** mode: only the cortex, in the same atlas and colours as the surface (DKT, or whichever **Other** atlas you picked). With **Plain** colour, nothing is painted.
- The **Atlas** box turns the whole overlay on or off. The words beside it say what is painted (for example "FreeSurfer DKT · cortex").
- Click **Regions ▸** to turn single regions on or off, grouped by lobe and side. Every region starts on. It is the same list as the one in the Brain panel, so a change in either place shows in both.
- Point anywhere on a painted region to read its name; pointing at a contact still names the contact.

### Extra scans side by side (T2, FLAIR, FA)

If the lab added extra scans, a **SHOW** bar runs across the top of the slice view. **Primary** is the main MRI (usually T1) that everything else is built on. The other buttons carry the names the lab gave the extra scans, such as T2, FLAIR or DTI color FA.

- Click a button to add or remove that scan as its own pane. All panes scroll together, so they always show the same slice.
- At least one pane always stays on.
- A scan marked **registering…** is still being lined up and cannot be shown yet.
- On colour FA panes, regions are drawn as white outlines so the colours stay readable.
- Comparing panes is also a quick alignment check: the same anatomy should sit in the same place in every pane.

### Fusion view (CT laid over MRI)

Fusion appears at the top of the left column when the patient has a CT that was lined up (registered) to the MRI. It overlays the CT on the MRI so you can judge how well they match.

- Pick **Axial**, **Sagittal** or **Coronal** with the buttons at the top.
- Drag the slider at the bottom from **MRI** to **CT**. While you sweep, the skull, ventricles and midline should stay in place.
- A banner says either **✓ Registration reviewed & confirmed** (green) or **⚠ Registration not yet reviewed** (orange). An unreviewed patient also shows **⚠ Reg. unreviewed** in the top bar and an orange dot on the Fusion picture.

The **Looks correct — Confirm** and **Looks off — Re-run precise** buttons are for editors and do nothing for you. If the match looks wrong, tell the lab which patient and which slice.

## Electrodes and contacts

A contact is named by its shaft's short name plus its number: \`LA3\` is contact 3 on shaft \`LA\`. When the lab gave the shaft a full label, it follows in brackets, for example \`LA3 (Left amygdala)\`.

### Which structure is this contact in?

1. Open a Completed patient.
2. In the Brain panel, click **⊕ Load** and wait for the coloured structures.
3. Make sure **Structures** (not Cortical surface) is selected.
4. Click the hover button at the top-left of the 3D view until it reads **contact → structure**.
5. Lower the **MRI Brain Surface** slider, or untick it, so deep contacts are easy to reach.
6. Point at the contact. Read the box in the top-right corner.

You can also point at a contact on any slice; the same box appears beside the cursor.

| The box shows | It means |
| --- | --- |
| A structure name only, e.g. **Left Hippocampus** | The contact sits inside that structure. |
| A structure name and **nearest · 1.4 mm** | The contact is just outside any labelled structure (often white matter or fluid), and that structure is the closest within 2 mm. |
| **unlabelled** | No labelled structure lies within 2 mm. |

### Which contacts are in this structure?

1. Load structures, then switch the hover button to **structure → contacts**.
2. Untick **Show brain structures**, open the list with **▸**, then tick only the structure you care about, for example Subcortical → Left → Hippocampus.
3. Point at it. The top-right box gives the count (for example **4 contacts inside**), and those contacts get a coloured ring.

### How far to trust the labels

Labels come from an automatic segmentation of the patient's own MRI. By default it uses the DKT atlas; if the lab uploaded FreeSurfer results, it may use those instead. White matter is not one of the named structures, so many depth contacts read as "nearest" rather than "inside". Near a boundary, scroll the slices around the contact and judge for yourself.

**Pictures for a case conference.** The app has no picture export. Set up the view you want, then press Windows + Shift + S to take a screenshot of that part of the screen.

## Viewing sEEG activity on the brain (experimental)

> **Experimental feature (beta).** This screen is still being developed and tested. Treat what it shows as preliminary: do not rely on it for clinical decisions, and expect its look and results to change. If something looks wrong, tell the lab.

The **◍ sEEG Activity** button in the top bar paints recorded sEEG signal onto each contact over time, in step with the traces. It is greyed out until the patient is Completed. It also needs a recording the lab has uploaded; viewers can watch recordings but cannot upload them.

The screen has three parts: settings on the left, the brain in the middle (with the same 3D / Sagittal / Axial / Coronal switcher), and the traces on the right.

### Settings (left)

- **Recording** — if there is more than one, pick it from the drop-down (listed by task or file name).
- **Frequency band** — **Review 1–70 (clinical)**, Delta 1–4, Theta 4–8, Alpha 8–13, Beta 13–30, Gamma 30–70, High-γ 70–150, or **Custom bandpass…** with your own limits in Hz.
- **Mapping mode**:
  - **Continuous** — the whole session; each contact is coloured by how far its band power is from its own average, as a z-score.
  - **Trial-averaged** — trials lined up on **Stimulus onset** or **Response onset** and averaged. Set the **Display window** (ms before and after the event) and the **Baseline** (always before the stimulus), then click **Apply**.
- **Activation (baseline z)** colour bar — the scale for the colours. **Limit ±z** fixes it yourself; **Auto** sets it from the data.
- **Brain surface opacity** and **Ignore contacts outside brain** — the second leaves contacts outside the brain in place but uncoloured.
- **Brain** structures — the same panel as in the main viewer.
- **Channel coverage** — how many recorded channels matched a contact (**mapped**) and which did not (**unmatched**).

### Traces (right)

- **▶** plays and **❚❚** pauses; the speed box sets how fast compared with real time.
- **z-score**, **filtered** or **raw** chooses what the traces show; **all** or **shaft** chooses which channels.
- **×** sets the trace height; **↔** sets how many seconds fit on screen, and **all** shows the whole recording.
- Type a time and press Enter to jump there. When zoomed in, the left and right arrow keys slide the time window; up and down scroll through channels.
- If a reviewer added markers, they are listed here and appear at the top-left of the brain as playback passes them.

Point at a contact on the brain or a trace to highlight the other. Computing a band can take a little while (**Computing band activity…**). Nothing here is saved. Click **← Back to reconstruction** at the bottom of the settings to return.

## A 15-minute practice tour

Do this once, on any Completed patient, and you will have used every feature a viewer has. You cannot break anything.

- [ ] **Sign in** with \`viewer\` and check the grey **viewer** tag at the top-right.
- [ ] **Open a patient** from the green **Completed** column.
- [ ] **Move the brain:** rotate with left-drag, zoom with the scroll wheel, slide it with right-drag. Press F5 if you get lost.
- [ ] **See inside:** drag the **MRI Brain Surface** slider down to about 10%, then untick it.
- [ ] **Load structures:** click **⊕ Load** in the Brain panel and wait for the colours.
- [ ] **Narrow it down:** untick **Show brain structures**, open the list with **▸**, then tick only Subcortical → Left → Hippocampus and Amygdala.
- [ ] **Name a contact:** set the hover button to **contact → structure** and point at a few contacts. Find one that is inside a structure and one that says **nearest**.
- [ ] **Count contacts:** set the hover button to **structure → contacts** and point at the hippocampus.
- [ ] **See the cortex:** click **Cortical surface** (coloured by DKT) and point at a few gyri. Open **Region colours** and untick one lobe to see it turn white. Then click **Structures** to go back.
- [ ] **Check the metal:** tick **CT Electrodes** and see that the coloured balls sit on the CT metal.
- [ ] **Read slices:** click **Axial** in the left column, scroll to a contact and point at it. Remember: the patient's right is on the screen's left.
- [ ] **Try the overlay:** in the **OVERLAY** bar, load the atlas, open **Regions ▸** and turn a few regions off and on.
- [ ] **Compare scans** (if a **SHOW** bar appears): turn on a second scan and scroll both together.
- [ ] **Check the registration** (if **Fusion** appears): open it and sweep the MRI–CT slider.
- [ ] **Optional, experimental: try the activity view** (if **◍ sEEG Activity** is lit and a recording exists): open it, press **▶**, then click **← Back to reconstruction**.
- [ ] **Finish:** click the **NeuroReconstruct** logo to return to the list, then the power-button symbol at the top-right to sign out.

## Troubleshooting

Most problems are fixed by a reload: press F5, or Ctrl+F5 to force a fresh copy of the page. If that does not help, find your problem below.

| What you see | What to do |
| --- | --- |
| The page will not open ("can't reach this page") | You are outside the Hopkins network. Connect to the Hopkins VPN and try again. |
| **Invalid username or password** | Retype slowly with Caps Lock off. If it still fails, the password may have changed; ask the lab. |
| Images or the brain stopped loading after a long session | Your 8-hour sign-in ran out. Press F5 and sign in again. |
| The browser's Back button does not return to the list | Click the **NeuroReconstruct** logo at the top-left instead. |
| A card shows a spinner and a "please wait" message | The scans are still processing. Try again in a few minutes. |
| The brain and the coloured structures do not line up, or a patient looks out of date | Press Ctrl+F5 to clear the old copy your browser kept. |
| The 3D area stays black or blank | Use an up-to-date Chrome or Edge, or try another computer. |
| No hover button at the top-left of the 3D view | Load structures first (**⊕ Load**), and pick **Structures** rather than Cortical surface. |
| Deep contacts are hidden | Switch from Cortical surface to **Structures**, or lower the **MRI Brain Surface** slider. |
| No colours on the slices | Click **⊕ Load atlas** in the **OVERLAY** bar (Structures mode), or pick a colour other than **Plain** (Cortical surface mode). Check that **Atlas** is ticked and that the regions you want are on under **Regions ▸**. |
| **◍ sEEG Activity** is greyed out | The patient is not Completed yet. |
| The sEEG screen is empty, or **Upload h5** fails | No recording has been uploaded for this patient, and viewers cannot upload. Ask the lab. |
| **Confirm** or **Re-run precise** in Fusion does nothing, or says "failed to start" | Those buttons are for editors. Report any alignment worry to the lab. |
| **Failed to load reconstruction**, **Mesh extraction failed**, or **Could not load … slice** | Press F5 once. If it persists, send the lab the patient ID and what you clicked. |

**Who to contact:** JJ Kim (<jkim605@jhmi.edu>). Include the patient ID, the view you were in, and a screenshot.

## Glossary

| Term | Meaning |
| --- | --- |
| Reconstruction | One patient's record in the app: MRI, CT, electrode positions and the 3D brain. |
| Shaft | One implanted electrode: a depth lead, a grid or a strip. Has a short name (\`LA\`) and often a full label. |
| Contact | One recording point on a shaft, named short name + number (\`LA3\`). |
| Depth (sEEG) / ECoG | Depth leads go into the brain and show as small balls; grids and strips sit on the surface and show as larger balls. |
| In Progress / Completed | In Progress: the lab is still placing and checking contacts. Completed: finished and locked. |
| Primary MRI | The main scan (usually T1) that the 3D brain, labels and exports are built from. |
| Secondary scans | Extra scans the lab added for viewing only, such as T2, FLAIR or FA. |
| Registration (co-registration) | Lining up the post-implant CT with the MRI so the metal lands on the right anatomy. |
| Fusion | The view that lays the CT over the MRI so you can check the registration. |
| HU (Hounsfield units) | The brightness scale of CT. Bone is roughly 1000 HU; electrode metal is usually much higher. |
| Floor / Ceiling | The range of CT brightness drawn by **CT Electrodes**. |
| Parcellation / atlas | Dividing the brain into named regions, and the set of region names used. |
| DKT | The Desikan–Killiany–Tourville atlas, the default set of region names applied to each patient's own MRI. |
| FreeSurfer | A standard research package. If the lab uploaded its results, region names and the cortical surface may come from it instead, with the Desikan-Killiany and Destrieux atlases as extra colour choices under **Other**. |
| Structures / Cortical surface | Two ways to draw the brain: separate see-through regions, or one solid folded surface. |
| FA (fractional anisotropy) | A DTI map of white-matter integrity; the colour version also shows fibre direction. |
| MNI space | A standard template brain, so positions can be compared across patients. |
| Radiological convention | The patient's right appears on the left of the screen. |
| z-score | How far a value is from its baseline, in standard deviations. |
| Viewer / Editor / Admin | Read-only users / users who build and edit reconstructions / users who also manage accounts. |
| Share link | A link that opens one patient without a login. Only editors can make one. |
`;
