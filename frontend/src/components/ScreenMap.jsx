import React from 'react';

// The user guide's diagram of the patient screen: where the top bar, the view
// switcher, the main view and the side panel sit. Same drawing as the shared
// guide doc, in the app's dark palette.
const ink = '#e8edf2';
const quiet = '#7a8a99';
const edge = '#4a5568';
const accent = '#00d4ff';

const box = (x, y, w, h) => <rect x={x} y={y} width={w} height={h} rx="8" fill="none" stroke={edge} strokeWidth="1.25" />;
const chip = (x, y, w) => <rect x={x} y={y} width={w} height="26" rx="6" fill="none" stroke={edge} strokeWidth="1" />;

export default function ScreenMap() {
  return (
    <svg viewBox="0 0 760 536" role="img" aria-label="Pick a view on the left, look in the middle, adjust on the right"
      fontSize="11.5" fontFamily="IBM Plex Sans, sans-serif" style={{ width: '100%', height: 'auto', display: 'block' }}>
      <text x="24" y="34" fontSize="15" fontWeight="600" fill={ink}>Pick a view on the left, look in the middle, adjust on the right</text>

      <g>
        {box(24, 56, 712, 108)}
        <text x="40" y="80" fontSize="13" fontWeight="600" fill={ink}>Top bar</text>
        {chip(40, 92, 128)}<text x="104" y="109" textAnchor="middle" fill={ink}>NeuroReconstruct</text>
        {chip(176, 92, 100)}<text x="226" y="109" textAnchor="middle" fill={ink}>ID · status</text>
        {chip(336, 92, 124)}<text x="398" y="109" textAnchor="middle" fill={ink}>◍ sEEG Activity</text>
        {chip(468, 92, 160)}<text x="548" y="109" textAnchor="middle" fill={ink}>⬇ Download MNI export</text>
        {chip(636, 92, 84)}<text x="678" y="109" textAnchor="middle" fill={ink}>viewer</text>
        <text x="40" y="146" fill={quiet}>Logo: back to the list. sEEG Activity is experimental (beta). MNI export is for research; skip it.</text>
      </g>

      <g>
        {box(24, 180, 148, 304)}
        <text x="40" y="204" fontSize="13" fontWeight="600" fill={ink}>Views</text>
        {chip(40, 220, 116)}<text x="98" y="237" textAnchor="middle" fill={ink}>Fusion (if CT)</text>
        {chip(40, 254, 116)}<text x="98" y="271" textAnchor="middle" fill={ink}>3D</text>
        {chip(40, 288, 116)}<text x="98" y="305" textAnchor="middle" fill={ink}>Sagittal</text>
        {chip(40, 322, 116)}<text x="98" y="339" textAnchor="middle" fill={ink}>Axial</text>
        {chip(40, 356, 116)}<text x="98" y="373" textAnchor="middle" fill={ink}>Coronal</text>
        <text x="40" y="410" fill={quiet}>Click one to show</text>
        <text x="40" y="426" fill={quiet}>it in the middle.</text>
        <text x="40" y="450" fill={quiet}>Fusion = CT on MRI</text>
      </g>

      <g>
        <rect x="188" y="180" width="312" height="304" rx="8" fill={accent} fillOpacity="0.08" stroke={accent} strokeWidth="2" />
        <text x="204" y="204" fontSize="13" fontWeight="600" fill={ink}>Main view</text>
        <text x="204" y="224" fill={ink}>The 3D brain, a slice, or Fusion,</text>
        <text x="204" y="240" fill={ink}>whichever you picked on the left.</text>
        {chip(204, 256, 112)}<text x="260" y="273" textAnchor="middle" fill={ink}>Hover mode ⇄</text>
        {chip(372, 256, 112)}<text x="428" y="273" textAnchor="middle" fill={ink}>Region name</text>
        <ellipse cx="344" cy="334" rx="72" ry="36" fill="none" stroke={edge} strokeWidth="1" strokeDasharray="4 4" />
        <text x="344" y="338" textAnchor="middle" fill={quiet}>brain or slice</text>
        {chip(204, 392, 132)}<text x="270" y="409" textAnchor="middle" fill={ink}>Shafts · contacts</text>
        {chip(372, 392, 112)}<text x="428" y="409" textAnchor="middle" fill={ink}>Mouse hints</text>
        <text x="204" y="446" fill={ink}>Left-drag rotates, right-drag pans,</text>
        <text x="204" y="462" fill={ink}>scroll zooms or steps through slices.</text>
      </g>

      <g>
        {box(516, 180, 220, 304)}
        <text x="532" y="204" fontSize="13" fontWeight="600" fill={ink}>Side panel</text>
        {box(532, 216, 188, 72)}
        <text x="548" y="240" fontSize="13" fontWeight="600" fill={ink}>Display</text>
        <text x="548" y="256" fill={ink}>MRI surface + slider</text>
        <text x="548" y="272" fill={ink}>CT Electrodes + HU range</text>
        {box(532, 296, 188, 88)}
        <text x="548" y="320" fontSize="13" fontWeight="600" fill={ink}>Brain</text>
        <text x="548" y="336" fill={ink}>⊕ Load structures</text>
        <text x="548" y="352" fill={ink}>Structures or Cortical</text>
        <text x="548" y="368" fill={ink}>Tick regions on or off</text>
        {box(532, 392, 188, 72)}
        <text x="548" y="416" fontSize="13" fontWeight="600" fill={ink}>Electrodes</text>
        <text x="548" y="432" fill={ink}>Colour, name, label</text>
        <text x="548" y="448" fill={ink}>and contacts placed</text>
      </g>

      <text x="24" y="512" fill={quiet}>In Progress patients show the editing panel on the right instead. Nothing you change there is saved.</text>
    </svg>
  );
}
