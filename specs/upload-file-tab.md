# Upload File Tab — Implementation Plan

## Overview

Add a dedicated **"Upload File"** tab as the second tab (index 1) alongside the existing "Quick Record" tab. This gives file upload a proper home with drag-and-drop support, file info preview, model selection, and its own transcription display — rather than being buried in the File menu.

The File → Upload Audio File menu item will remain but will switch to the Upload tab and trigger the file picker from there.

---

## UI Design

```
┌─────────────────────────────────────────────┐
│  [ Quick Record ]  [ Upload File ]          │
├─────────────────────────────────────────────┤
│                                             │
│  ┌─ Transcription Model ─────────────────┐  │
│  │  [  Local Whisper (base)       ▼  ]   │  │
│  └───────────────────────────────────────┘  │
│                                             │
│  ┌─ Drop Zone ───────────────────────────┐  │
│  │                                       │  │
│  │     ♪  Drag & drop audio file here    │  │
│  │         or click to browse            │  │
│  │                                       │  │
│  │   Supported: WAV, MP3, M4A, OGG,     │  │
│  │             FLAC, WMA                 │  │
│  └───────────────────────────────────────┘  │
│                                             │
│  ── After file selected ──────────────────  │
│                                             │
│  ┌─ File Info ───────────────────────────┐  │
│  │  meeting_recording.wav                │  │
│  │  Size: 45.2 MB  Duration: 12m 34s    │  │
│  │  44100 Hz, Mono                       │  │
│  │  ⚠ Will be split into 3 chunks       │  │
│  │                                       │  │
│  │  [ Remove ]           [ Transcribe ]  │  │
│  └───────────────────────────────────────┘  │
│                                             │
│  Ready to transcribe                        │
│                                             │
│  ┌─ Transcription ───────────────────────┐  │
│  │  Transcription will appear here...    │  │
│  │                                       │  │
│  └───────────────────────────────────────┘  │
│                                             │
│  [ Stats: 8.2s | 12m 34s | 45.2 MB ]       │
│                                             │
└─────────────────────────────────────────────┘
```

### States

1. **Empty** — Drop zone is visible. No file info card. Transcription area shows placeholder.
2. **File Selected** — Drop zone hides, file info card appears with details + Transcribe/Remove buttons.
3. **Transcribing** — Transcribe button disabled, status shows progress, overlay shows processing state.
4. **Complete** — Transcript displays in text area, stats widget appears. File info remains so user can re-transcribe or remove.

---

## Files to Modify

### New File: `ui_qt/widgets/upload_file_tab.py`

New `UploadFileTab(QWidget)` widget, structured like `QuickRecordTab`:

**Signals:**
- `upload_requested(str)` — emitted with audio_path when user clicks Transcribe
- `model_changed(str)` — model dropdown changed (same as QuickRecordTab)

**Key Widgets:**
- `model_combo` (QComboBox) — same model selector as QuickRecordTab
- `drop_zone` (custom `DropZoneWidget`) — drag-and-drop area that also acts as a browse button
- `file_info_card` (QFrame) — shows file details after selection, with Remove and Transcribe buttons
- `status_label` (QLabel) — status text
- `transcription_text` (QTextEdit) — transcript display (read-only)
- `stats_widget` (TranscriptionStatsWidget) — reused from existing widget

**Key Methods (public API):**
- `set_status(text)` — update status label
- `set_transcript(text)` — set transcript text
- `set_transcription_stats(...)` — forward to stats widget
- `clear_transcription()` / `clear_transcription_stats()` — reset display
- `set_model_selection(model_value)` — sync model dropdown
- `set_file(audio_path)` — programmatically set a file (for menu redirect)
- `clear_file()` — reset to empty/drop-zone state
- `set_transcribing(bool)` — toggle UI into transcribing/idle state

**Drop Zone sub-widget** (`DropZoneWidget(QFrame)` — inner class or same file):
- Accepts drag events with file MIME type filtering (audio files only)
- Visual feedback: border highlight on drag-over
- Click opens `QFileDialog` with audio filters
- Emits `file_dropped(str)` signal with the selected file path

**Drag-and-drop implementation:**
```python
def dragEnterEvent(self, event):
    if event.mimeData().hasUrls():
        for url in event.mimeData().urls():
            if url.toLocalFile().lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac', '.wma')):
                event.acceptProposedAction()
                return
    event.ignore()

def dropEvent(self, event):
    for url in event.mimeData().urls():
        path = url.toLocalFile()
        if path.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac', '.wma')):
            self.file_dropped.emit(path)
            break
```

**File info display** — reuse logic from `UploadPreviewDialog` but inline:
- Call `audio_processor.preview_file(path)` to get `AudioFilePreview`
- Display filename, size, duration, sample rate, channels
- Show chunk warning if `needs_splitting`

---

### Modify: `ui_qt/widgets/tabbed_content.py`

1. Add tab index constant:
   ```python
   TAB_UPLOAD_FILE = 1
   ```

2. Add the tab to the tab bar in `_setup_ui()`:
   ```python
   self.tab_bar.addTab("Upload File")
   ```

No other changes needed — `add_tab()` and `sync_stack_with_tab_bar()` already handle the stack generically.

---

### Modify: `ui_qt/main_window.py`

1. Import `UploadFileTab`
2. In `_setup_ui()`, after creating `quick_record_tab`:
   ```python
   self.upload_file_tab = UploadFileTab()
   self.tabbed_content.add_tab(self.upload_file_tab, "Upload File")
   ```
3. Add new signal:
   ```python
   upload_file_requested = pyqtSignal(str)  # audio_path from upload tab
   ```
4. Connect `upload_file_tab.upload_requested` → emit `upload_file_requested`
5. Connect `upload_file_tab.model_changed` → existing `_on_model_changed`
6. Update `upload_audio_file()` (File menu handler) to switch to the Upload tab and trigger browse:
   ```python
   def upload_audio_file(self):
       self.tabbed_content.set_current_index(TabbedContentWidget.TAB_UPLOAD_FILE)
       self.upload_file_tab.open_file_browser()
   ```

---

### Modify: `ui_qt/ui_controller.py`

1. Connect the new `upload_file_requested` signal:
   ```python
   self.main_window.upload_file_requested.connect(self._on_upload_file_tab_transcribe)
   ```
2. New handler `_on_upload_file_tab_transcribe(audio_path)`:
   - Calls `self.on_upload_audio(audio_path)` (same callback the dialog used to call)
   - No preview dialog needed — the tab already shows the file preview inline
3. Add accessor `get_upload_file_tab()` (mirrors existing `get_quick_record_tab()`)
4. Update `_display_transcript` / status methods to route to the correct tab based on which tab is active (or which tab initiated the transcription)
5. Remove or keep `open_upload_audio_dialog()` — the File menu now redirects to the tab, so the old dialog flow becomes unused. Keep for backward compatibility initially, remove in follow-up cleanup.

---

### Modify: `ui_qt/widgets/__init__.py`

Add export:
```python
from ui_qt.widgets.upload_file_tab import UploadFileTab
```

---

### Modify: `services/settings.py` (minor)

No changes needed — `LAST_TAB_INDEX` already handles tab persistence generically. Index 1 will auto-persist.

---

## Implementation Notes

### Model Sync Between Tabs
Both tabs have model dropdowns. They should stay in sync:
- When the user changes model on either tab, both combos update
- The `ui_controller._on_model_changed` handler already exists — just connect both tabs' signals and add a method to sync the other tab's combo

### Transcript Routing
The current flow sends transcription results to `main_window.set_transcript()`, which delegates to `quick_record_tab.set_transcript()`. For upload-tab-initiated transcriptions, results need to go to `upload_file_tab.set_transcript()` instead.

**Approach:** Track which tab initiated the current transcription in `ui_controller` (e.g., `self._transcription_source_tab`). When the transcript arrives, route to the correct tab.

### Tab Locking During Transcription
`TabbedContentWidget.set_recording_state()` already disables other tabs during recording. Extend this concept: when a file upload transcription is in progress, disable the Quick Record tab (and vice versa). The existing `set_recording_state(is_recording, source_tab)` API already supports this — just call it with `TAB_UPLOAD_FILE` as the source.

### Drag-and-Drop Edge Cases
- Multiple files dropped: use only the first valid audio file
- Non-audio files: ignore with visual feedback (drop zone border turns red briefly)
- File disappears between drop and transcribe: handle `FileNotFoundError` gracefully

---

## Verification Steps

1. **Tab renders** — Launch app, verify "Upload File" tab appears next to "Quick Record"
2. **Tab persistence** — Switch to Upload File tab, restart app, verify it restores to Upload File
3. **Drag and drop** — Drag a .wav file onto the drop zone, verify file info appears
4. **Click to browse** — Click drop zone, verify file dialog opens with audio filters
5. **File info display** — Select a file, verify name/size/duration/format shown correctly
6. **Large file warning** — Select a file >23MB, verify chunk count displayed
7. **Transcribe flow** — Click Transcribe, verify overlay appears, transcript shows in upload tab (not quick record tab)
8. **Remove file** — Click Remove, verify drop zone reappears
9. **Model sync** — Change model on upload tab, verify quick record tab updates (and vice versa)
10. **File menu redirect** — Use File → Upload Audio File, verify it switches to Upload tab and opens browse
11. **Tab lock** — Start recording on Quick Record, verify Upload tab is disabled (and vice versa during transcription)
12. **Non-audio drag rejection** — Drag a .txt file, verify it's rejected
