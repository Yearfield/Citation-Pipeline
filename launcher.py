"""
Citation Pipeline — GUI Launcher
Double-click "Launch Citation Pipeline.bat" to start this.
No terminal knowledge required.
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading, subprocess, sys, os, webbrowser, json, time, logging, re

# ── Resolve paths relative to this file ─────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(BASE_DIR, 'venv', 'Scripts', 'python.exe')
VENV_EXISTS = os.path.exists(VENV_PYTHON)
if not VENV_EXISTS:
    VENV_PYTHON = sys.executable  # fallback — will show warning on startup

CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'settings.json')
TEMP_DIR    = os.path.join(BASE_DIR, 'temp')
REVIEW_PORT = 8000
server_proc = None

# ── Logging setup ─────────────────────────────────────────────────────────────
_LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(os.path.join(_LOG_DIR, 'launcher.log'), encoding='utf-8'),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger('citation_pipeline')

# ── Settings helpers ─────────────────────────────────────────────────────────
def load_settings():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def save_settings(settings):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        logger.error('Failed to save settings: %s', e)
        raise

# ── Subprocess helpers ───────────────────────────────────────────────────────
_QUIET_ENV = {
    **os.environ,
    'HF_HUB_DISABLE_TELEMETRY': '1',
    'TRANSFORMERS_VERBOSITY': 'error',
    'SAFETENSORS_FAST_GPU': '0',
}

def run_in_pipeline_dir(cmd_args, log_widget, on_done=None):
    """Run a subprocess in BASE_DIR and stream its output to the log widget.

    All widget updates are marshalled to the main thread via widget.after(),
    making this safe to call from any thread.
    """
    def _run():
        try:
            proc = subprocess.Popen(
                cmd_args, cwd=BASE_DIR, env=_QUIET_ENV,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace'
            )
            for line in proc.stdout:
                _log(log_widget, line.rstrip())
            proc.wait()
            if proc.returncode != 0:
                _log(log_widget, f'⚠ Process exited with error code {proc.returncode}')
            logger.info('Subprocess finished (exit code %d): %s',
                        proc.returncode, ' '.join(str(a) for a in cmd_args))
        except Exception as e:
            _log(log_widget, f'\nERROR: {e}\n')
            logger.error('Subprocess error (%s): %s', cmd_args[0] if cmd_args else '?', e)
        finally:
            if on_done:
                # Marshal the completion callback to the main thread.
                log_widget.after(0, on_done)
    threading.Thread(target=_run, daemon=True).start()

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
_TQDM_RE = re.compile(r'^\s*\S+.*\|.*\|\s*\d+/\d+')   # e.g.  "Batches:  50%|#####     | 1/2"

def _log(widget, msg):
    """Append msg to the log widget.

    Safe to call from any thread — widget updates are always scheduled on the
    main thread via widget.after(), which is the only thread-safe way to
    update tkinter widgets.

    ANSI escape codes are stripped and tqdm-style progress bars are collapsed
    into a single updating line instead of flooding the log.
    """
    clean = _ANSI_RE.sub('', msg).strip()
    if clean:
        logger.info(clean)
    is_progress = bool(_TQDM_RE.match(clean))

    def _update():
        widget.configure(state='normal')
        if is_progress:
            # Replace the last line if it was also a progress bar.
            prev_start = widget.index('end-2l linestart')
            prev_end   = widget.index('end-2l lineend')
            prev_text  = widget.get(prev_start, prev_end)
            if _TQDM_RE.match(prev_text):
                widget.delete(prev_start, prev_end + '+1c')
        line = clean if clean.endswith('\n') else clean + '\n'
        widget.insert(tk.END, line)
        widget.see(tk.END)
        widget.configure(state='disabled')

    widget.after(0, _update)

def start_review_server(log_widget):
    global server_proc
    if server_proc and server_proc.poll() is None:
        return
    _log(log_widget, 'Starting review server...')
    try:
        log_path = os.path.join(BASE_DIR, 'logs', 'server.log')
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log_file = open(log_path, 'a')
        server_env = {
            **os.environ,
            'TQDM_DISABLE': '1',
            'TRANSFORMERS_VERBOSITY': 'error',
            'HF_HUB_DISABLE_TELEMETRY': '1',
            'TOKENIZERS_PARALLELISM': 'false',
        }
        server_proc = subprocess.Popen(
            [VENV_PYTHON, '-m', 'uvicorn', 'review.app:app',
             '--port', str(REVIEW_PORT), '--reload'],
            cwd=BASE_DIR,
            stdout=log_file, stderr=log_file,
            env=server_env
        )
        _log(log_widget, f'Review server running at http://localhost:{REVIEW_PORT}')
        _log(log_widget, f'Server logs: logs/server.log')
    except Exception as e:
        _log(log_widget, f'Could not start server: {e}')

def get_last_session(doc_name=None):
    if not os.path.isdir(TEMP_DIR):
        return None
    sessions = sorted([f for f in os.listdir(TEMP_DIR) if f.startswith('session_')], reverse=True)
    for sf in sessions:
        try:
            with open(os.path.join(TEMP_DIR, sf)) as f:
                s = json.load(f)
            if doc_name is None or s.get('document') == doc_name:
                return s['session_id']
        except Exception:
            pass
    return None

# ── Main Window ──────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Citation Pipeline')
        self.geometry('860x700')
        self.resizable(True, True)
        self.configure(bg='#1a1a2e')
        self._build_ui()
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self.after(100, self._startup_check)

    # ── UI Construction ───────────────────────────────────────────────────────
    def _build_ui(self):
        settings = load_settings()

        # Header
        hdr = tk.Frame(self, bg='#1a1a2e', pady=10)
        hdr.pack(fill='x', padx=20)
        tk.Label(hdr, text='Citation Pipeline', font=('Segoe UI', 15, 'bold'),
                 fg='#fff', bg='#1a1a2e').pack(side='left')
        tk.Label(hdr, text='Academic citation assistant',
                 font=('Segoe UI', 9), fg='#aaa', bg='#1a1a2e').pack(side='left', padx=10)

        # ── Install banner (shown only when venv is missing) ──────────────────
        if not VENV_EXISTS:
            banner = tk.Frame(self, bg='#7b2d00', pady=8, padx=14)
            banner.pack(fill='x', padx=20, pady=(0, 4))
            tk.Label(banner,
                     text='  First-time setup required — packages are not yet installed.',
                     fg='#ffe0b2', bg='#7b2d00', font=('Segoe UI', 9, 'bold')).pack(side='left')
            tk.Button(banner, text='Install Dependencies Now',
                      command=self._run_install,
                      bg='#ff6d00', fg='#fff', relief='flat',
                      font=('Segoe UI', 9, 'bold'), padx=14, pady=4,
                      cursor='hand2').pack(side='right')

        # ── Step 1: Library Setup ─────────────────────────────────────────────
        step1 = tk.LabelFrame(self, text='  Step 1 — Source Library  ',
                               fg='#e94560', bg='#16213e', font=('Segoe UI', 9, 'bold'),
                               bd=1, relief='groove', pady=8, padx=10)
        step1.pack(fill='x', padx=20, pady=(0, 6))

        tk.Label(step1,
                 text='Point to your exported .bib file and the folder containing your source PDFs.\n'
                      'Export your library from Zotero: File > Export Library > Better BibTeX (tick "Keep Updated").\n'
                      'Migrating from Mendeley? Import into Zotero first: File > Import > Mendeley.',
                 fg='#aaa', bg='#16213e', font=('Segoe UI', 8),
                 justify='left').grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 8))

        # .bib file row
        tk.Label(step1, text='Library file (.bib):', fg='#ccc', bg='#16213e',
                 font=('Segoe UI', 9), width=18, anchor='w').grid(row=1, column=0, sticky='w')
        self.bib_var = tk.StringVar(value=self._abs(settings.get('bib_path', 'sources/library.bib')))
        tk.Entry(step1, textvariable=self.bib_var, width=52,
                 font=('Segoe UI', 9)).grid(row=1, column=1, padx=6, sticky='ew')
        tk.Button(step1, text='Browse…', command=self._browse_bib,
                  bg='#e94560', fg='#fff', relief='flat', font=('Segoe UI', 9),
                  padx=8).grid(row=1, column=2)

        # PDFs folder row
        tk.Label(step1, text='PDFs folder:', fg='#ccc', bg='#16213e',
                 font=('Segoe UI', 9), width=18, anchor='w').grid(row=2, column=0, sticky='w', pady=(6, 0))
        self.pdfs_var = tk.StringVar(value=self._abs(settings.get('pdfs_dir', 'sources/pdfs')))
        tk.Entry(step1, textvariable=self.pdfs_var, width=52,
                 font=('Segoe UI', 9)).grid(row=2, column=1, padx=6, sticky='ew', pady=(6, 0))
        tk.Button(step1, text='Browse…', command=self._browse_pdfs,
                  bg='#e94560', fg='#fff', relief='flat', font=('Segoe UI', 9),
                  padx=8).grid(row=2, column=2, pady=(6, 0))

        step1.columnconfigure(1, weight=1)

        # Status + Save row
        bottom = tk.Frame(step1, bg='#16213e')
        bottom.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(8, 0))
        self.setup_status = tk.Label(bottom, text='', fg='#ffc107', bg='#16213e',
                                      font=('Segoe UI', 8, 'italic'))
        self.setup_status.pack(side='left')
        tk.Button(bottom, text='Save & Build Index', command=self._save_and_build,
                  bg='#007bff', fg='#fff', relief='flat', font=('Segoe UI', 9, 'bold'),
                  padx=14, pady=5, cursor='hand2').pack(side='right')
        tk.Button(bottom, text='Force Full Rebuild', command=self._save_and_force_rebuild,
                  bg='#e94560', fg='#fff', relief='flat', font=('Segoe UI', 9, 'bold'),
                  padx=10, pady=5, cursor='hand2').pack(side='right', padx=(0, 6))
        tk.Button(bottom, text='Save paths only', command=self._save_paths,
                  bg='#495057', fg='#fff', relief='flat', font=('Segoe UI', 9),
                  padx=10, pady=5, cursor='hand2').pack(side='right', padx=(0, 6))

        # ── Step 2: Process a Document ────────────────────────────────────────
        step2 = tk.LabelFrame(self, text='  Step 2 — Process a Document  ',
                               fg='#28a745', bg='#16213e', font=('Segoe UI', 9, 'bold'),
                               bd=1, relief='groove', pady=8, padx=10)
        step2.pack(fill='x', padx=20, pady=(0, 6))

        # Document file picker
        tk.Label(step2, text='Document (.md):', fg='#ccc', bg='#16213e',
                 font=('Segoe UI', 9), width=18, anchor='w').grid(row=0, column=0, sticky='w')
        self.doc_var = tk.StringVar()
        tk.Entry(step2, textvariable=self.doc_var, width=52,
                 font=('Segoe UI', 9)).grid(row=0, column=1, padx=6, sticky='ew')
        tk.Button(step2, text='Browse…', command=self._browse_doc,
                  bg='#28a745', fg='#fff', relief='flat', font=('Segoe UI', 9),
                  padx=8).grid(row=0, column=2)

        # Style profile picker
        tk.Label(step2, text='Citation style:', fg='#ccc', bg='#16213e',
                 font=('Segoe UI', 9), width=18, anchor='w').grid(row=1, column=0, sticky='w', pady=(6, 0))
        self.profile_var = tk.StringVar()
        profiles = self._list_profiles()
        self.profile_combo = ttk.Combobox(step2, textvariable=self.profile_var,
                                           values=profiles, width=49,
                                           font=('Segoe UI', 9), state='readonly')
        if profiles:
            self.profile_combo.set(profiles[0])
        self.profile_combo.grid(row=1, column=1, padx=6, sticky='ew', pady=(6, 0))

        step2.columnconfigure(1, weight=1)

        # Action buttons row
        btn_frame = tk.Frame(step2, bg='#16213e')
        btn_frame.grid(row=2, column=0, columnspan=3, sticky='ew', pady=(10, 0))

        actions = [
            ('Import Chrome Tabs', self._import_tabs, '#17a2b8'),
            ('Process Document',   self._process,     '#28a745'),
            ('Open Review Panel',  self._open_review, '#fd7e14'),
            ('Export Document',    self._export,      '#6f42c1'),
        ]
        for text, cmd, color in actions:
            tk.Button(btn_frame, text=text, command=cmd,
                      bg=color, fg='#fff', relief='flat',
                      font=('Segoe UI', 9, 'bold'), padx=12, pady=6,
                      cursor='hand2').pack(side='left', padx=(0, 6))

        tk.Label(step2, text='Import open browser tabs as sources before processing your document.',
                 fg='#aaa', bg='#16213e', font=('Segoe UI', 8)).grid(
            row=3, column=0, columnspan=3, sticky='w', pady=(6, 0))

        # ── Log panel ─────────────────────────────────────────────────────────
        log_frame = tk.Frame(self, bg='#1a1a2e')
        log_frame.pack(fill='both', expand=True, padx=20, pady=(4, 0))
        tk.Label(log_frame, text='Activity Log', fg='#555', bg='#1a1a2e',
                 font=('Segoe UI', 8, 'italic')).pack(anchor='w')
        self.log_box = scrolledtext.ScrolledText(
            log_frame, state='disabled', font=('Consolas', 9),
            bg='#0f0f1a', fg='#cccccc', relief='flat', wrap='word', height=14
        )
        self.log_box.pack(fill='both', expand=True)

        # Status bar with progress indicator
        status_bar = tk.Frame(self, bg='#0f0f1a')
        status_bar.pack(fill='x', side='bottom')
        self.status_var = tk.StringVar(value='Ready')
        tk.Label(status_bar, textvariable=self.status_var, fg='#aaa', bg='#0f0f1a',
                 anchor='w', font=('Segoe UI', 8), pady=4, padx=10).pack(side='left', fill='x', expand=True)
        style = ttk.Style()
        style.configure('Pipeline.Horizontal.TProgressbar',
                        troughcolor='#1a1a2e', background='#e94560')
        self.progress = ttk.Progressbar(
            status_bar, mode='indeterminate', length=140,
            style='Pipeline.Horizontal.TProgressbar'
        )
        # progress bar is hidden until _start_busy() is called

        _log(self.log_box, 'Citation Pipeline ready.')
        _log(self.log_box, f'Pipeline folder: {BASE_DIR}')

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _abs(self, path):
        """Convert a settings-relative path to an absolute path for display."""
        if os.path.isabs(path):
            return path
        return os.path.normpath(os.path.join(BASE_DIR, path))

    def _rel(self, path):
        """Convert an absolute path to a settings-relative path if inside BASE_DIR."""
        try:
            return os.path.relpath(path, BASE_DIR).replace('\\', '/')
        except ValueError:
            return path  # On a different drive — keep absolute

    def _list_profiles(self):
        config_dir = os.path.join(BASE_DIR, 'config')
        profiles = []
        if os.path.isdir(config_dir):
            profiles = [f for f in os.listdir(config_dir)
                        if f.endswith('_profile.json') or f == 'style_profile.json']
        # Also include styles from the persistent styles library
        lib_path = os.path.join(config_dir, 'styles_library.json')
        if os.path.isfile(lib_path):
            try:
                with open(lib_path) as f:
                    library = json.load(f)
                for style in library.values():
                    label = f"[Library] {style.get('name', style.get('id', '?'))}"
                    profiles.append(label)
            except Exception:
                pass
        return profiles

    def _resolve_profile_path(self, profile_label):
        """Resolve a profile dropdown label to a file path.

        For legacy file-based profiles, returns config/<filename>.
        For library styles (prefixed with [Library]), writes the style
        to a temp file and returns that path.
        """
        if not profile_label:
            return ''
        if profile_label.startswith('[Library] '):
            style_name = profile_label[len('[Library] '):]
            lib_path = os.path.join(BASE_DIR, 'config', 'styles_library.json')
            try:
                with open(lib_path) as f:
                    library = json.load(f)
                for style in library.values():
                    name = style.get('name', style.get('id', ''))
                    if name == style_name:
                        active_path = os.path.join(BASE_DIR, 'config', '_active_style.json')
                        with open(active_path, 'w') as f:
                            json.dump(style, f, indent=2)
                        return active_path
            except Exception:
                pass
            return ''
        return os.path.join(BASE_DIR, 'config', profile_label)

    def _set_status(self, msg):
        self.status_var.set(msg)
        self.update_idletasks()

    def _start_busy(self):
        """Show and animate the progress bar."""
        self.progress.pack(side='right', padx=10, pady=4)
        self.progress.start(15)

    def _stop_busy(self):
        """Stop and hide the progress bar."""
        self.progress.stop()
        self.progress.pack_forget()

    def _update_setup_status(self):
        bib   = self.bib_var.get().strip()
        pdfs  = self.pdfs_var.get().strip()
        ok_b  = os.path.isfile(bib)
        ok_p  = os.path.isdir(pdfs)
        if ok_b and ok_p:
            try:
                count = len(os.listdir(pdfs))
            except OSError:
                count = '?'
            self.setup_status.config(
                text=f'✓  Library configured  ({count} PDF(s) found)',
                fg='#28a745')
        elif not ok_b and not ok_p:
            self.setup_status.config(
                text='⚠  .bib file and PDFs folder not found — browse to set them up',
                fg='#ffc107')
        elif not ok_b:
            self.setup_status.config(
                text='⚠  .bib file not found — browse to locate it',
                fg='#ffc107')
        else:
            self.setup_status.config(
                text='⚠  PDFs folder not found — browse to locate it',
                fg='#ffc107')

    def _startup_check(self):
        if not VENV_EXISTS:
            _log(self.log_box, '\n  FIRST-TIME SETUP REQUIRED')
            _log(self.log_box, '  Python packages are not installed yet.')
            _log(self.log_box, '  Click "Install Dependencies Now" in the orange banner above.')
            _log(self.log_box, '  This downloads ~600 MB and takes 10-20 minutes.\n')
            return
        self._update_setup_status()
        bib = self.bib_var.get().strip()
        if not os.path.isfile(bib):
            _log(self.log_box,
                 '\n  SETUP NEEDED: Your library .bib file was not found.')
            _log(self.log_box,
                 '  Use "Browse…" next to "Library file (.bib)" to point to your')
            _log(self.log_box,
                 '  Zotero export, then click "Save & Build Index".\n')

    # ── File pickers ──────────────────────────────────────────────────────────
    def _browse_bib(self):
        path = filedialog.askopenfilename(
            title='Select your Zotero/Mendeley .bib export file',
            filetypes=[('BibTeX files', '*.bib'), ('All files', '*.*')]
        )
        if path:
            self.bib_var.set(path)
            self._update_setup_status()

    def _browse_pdfs(self):
        path = filedialog.askdirectory(title='Select the folder containing your source PDFs')
        if path:
            self.pdfs_var.set(path)
            self._update_setup_status()

    def _browse_doc(self):
        path = filedialog.askopenfilename(
            title='Select the document you want to add citations to',
            filetypes=[('Markdown files', '*.md'), ('Text files', '*.txt'), ('All files', '*.*')]
        )
        if path:
            self.doc_var.set(path)

    # ── Settings persistence ──────────────────────────────────────────────────
    def _save_paths(self):
        bib  = self.bib_var.get().strip()
        pdfs = self.pdfs_var.get().strip()
        if not bib or not pdfs:
            messagebox.showwarning('Incomplete', 'Please fill in both the .bib file and PDFs folder.')
            return
        settings = load_settings()
        settings['bib_path']  = self._rel(bib)
        settings['pdfs_dir']  = self._rel(pdfs)
        try:
            save_settings(settings)
        except Exception as e:
            messagebox.showerror('Save failed', f'Could not save settings:\n{e}')
            return
        self._update_setup_status()
        _log(self.log_box, 'Library paths saved to config/settings.json.')
        self._set_status('Paths saved.')

    # ── Actions ───────────────────────────────────────────────────────────────
    def _require_venv(self):
        """Return True if venv is ready; otherwise show an error and return False."""
        if not os.path.exists(VENV_PYTHON) or not VENV_EXISTS:
            messagebox.showerror(
                'Not installed',
                'Python packages are not installed yet.\n\n'
                'Click "Install Dependencies Now" in the orange banner\n'
                'at the top of the window and wait for it to finish.'
            )
            return False
        return True

    def _run_install(self):
        install_bat = os.path.join(BASE_DIR, 'install.bat')
        if not os.path.exists(install_bat):
            messagebox.showerror('Not found', f'install.bat not found at:\n{install_bat}')
            return
        _log(self.log_box, '\n--- Running first-time installation ---')
        _log(self.log_box, 'This will take 10-20 minutes. Do not close this window.\n')
        self._set_status('Installing dependencies — please wait...')
        self._start_busy()

        def after_install():
            self._stop_busy()
            new_venv = os.path.join(BASE_DIR, 'venv', 'Scripts', 'python.exe')
            if os.path.exists(new_venv):
                self._set_status('Installation complete! Please restart the launcher.')
                messagebox.showinfo(
                    'Installation complete',
                    'All dependencies have been installed.\n\n'
                    'Please close and reopen the launcher to use the pipeline.'
                )
            else:
                self._set_status('Installation may have had errors — check the log above.')

        run_in_pipeline_dir(['cmd', '/c', install_bat, '/quiet'], self.log_box, on_done=after_install)

    def _wait_for_server(self, callback, fallback=None,
                          max_attempts=20, interval_ms=500):
        """Poll the review server until it responds, then invoke *callback*.

        Tries a lightweight GET to ``http://localhost:{REVIEW_PORT}/`` every
        *interval_ms* milliseconds for up to *max_attempts* times (~10 s by
        default).  On success, *callback* is called on the main thread.  If
        all attempts fail, *fallback* is called instead (or a log message is
        written if no fallback is provided).

        Uses ``self.after()`` so the Tk event loop stays responsive.
        """
        import urllib.request, urllib.error

        def _try(attempt):
            try:
                url = f'http://localhost:{REVIEW_PORT}/'
                urllib.request.urlopen(url, timeout=1)
                # 2xx — server is ready.
                _log(self.log_box, '  Server is ready.')
                self.after(0, callback)
                return
            except urllib.error.HTTPError:
                # 4xx / 5xx — server IS listening, just no route at /.
                _log(self.log_box, '  Server is ready.')
                self.after(0, callback)
                return
            except Exception:
                pass  # Connection refused / timeout — server not up yet

            if attempt < max_attempts:
                self.after(interval_ms, lambda: _try(attempt + 1))
            else:
                _log(self.log_box,
                     '  Server did not respond after '
                     f'{max_attempts * interval_ms / 1000:.0f} s.')
                if fallback:
                    self.after(0, fallback)

        # Kick off the first attempt after one interval.
        self.after(interval_ms, lambda: _try(1))

    def _save_and_build(self):
        """Save library paths then build the index.

        Preferred path: start the review server (if not already running),
        then POST an ``index_build`` task to ``/tasks/enqueue`` and poll
        ``/tasks/{task_id}`` for live progress.

        Fallback: if the server is not reachable within ~10 s, run
        ``pipeline/indexer.py`` as a direct subprocess (old behaviour).
        """
        if not self._require_venv():
            return
        bib  = self.bib_var.get().strip()
        pdfs = self.pdfs_var.get().strip()
        if not bib:
            messagebox.showwarning('Missing .bib file',
                                   'Please browse to your Zotero .bib export file first.')
            return
        if not os.path.isfile(bib):
            messagebox.showerror('File not found',
                                 f'The .bib file was not found:\n{bib}\n\n'
                                 'Export your library from Zotero:\n'
                                 'File > Export Library > Better BibTeX, tick "Keep Updated".')
            return
        if not pdfs or not os.path.isdir(pdfs):
            messagebox.showwarning('Missing PDFs folder',
                                   'Please browse to the folder that contains your source PDFs.')
            return

        self._save_paths()
        _log(self.log_box, '\n--- Building source index ---')
        _log(self.log_box, f'  .bib file : {bib}')
        _log(self.log_box, f'  PDFs folder: {pdfs}')
        self._set_status('Building index — this may take several minutes...')
        self._start_busy()

        # Start the review server so the task queue is available,
        # then poll until it is ready before enqueuing the build task.
        start_review_server(self.log_box)
        _log(self.log_box, '  Waiting for review server to start...')
        self._wait_for_server(
            callback=self._enqueue_index_build,
            fallback=self._index_build_subprocess,
        )

    def _save_and_force_rebuild(self):
        """Same as _save_and_build but passes force=True to wipe and recreate all indices."""
        if not self._require_venv():
            return
        bib  = self.bib_var.get().strip()
        pdfs = self.pdfs_var.get().strip()
        if not bib:
            messagebox.showwarning('Missing .bib file',
                                   'Please browse to your Zotero .bib export file first.')
            return
        if not os.path.isfile(bib):
            messagebox.showerror('File not found',
                                 f'The .bib file was not found:\n{bib}')
            return
        if not pdfs or not os.path.isdir(pdfs):
            messagebox.showwarning('Missing PDFs folder',
                                   'Please browse to the folder that contains your source PDFs.')
            return

        self._save_paths()
        _log(self.log_box, '\n--- Force Full Rebuild (wiping existing index) ---')
        _log(self.log_box, f'  .bib file : {bib}')
        _log(self.log_box, f'  PDFs folder: {pdfs}')
        self._set_status('Force rebuilding index — this will take several minutes...')
        self._start_busy()

        start_review_server(self.log_box)
        _log(self.log_box, '  Waiting for review server to start...')
        self._wait_for_server(
            callback=lambda: self._enqueue_index_build(force=True),
            fallback=self._index_build_subprocess,
        )

    def _index_build_subprocess(self):
        """Fallback: run the indexer as a direct subprocess."""
        _log(self.log_box, '  Running indexer directly (subprocess fallback).')
        run_in_pipeline_dir(
            [VENV_PYTHON, '-m', 'pipeline.indexer'],
            self.log_box,
            on_done=lambda: (
                self._stop_busy(),
                self._set_status('Index build complete.')
            ),
        )

    def _enqueue_index_build(self, force: bool = False):
        """POST an index_build task to the review server, or fall back to subprocess."""
        import urllib.request, urllib.error
        url  = f'http://localhost:{REVIEW_PORT}/tasks/enqueue'
        body = json.dumps({'task_type': 'index_build',
                           'payload':   {'force': force}}).encode()
        req  = urllib.request.Request(
            url, data=body,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data    = json.loads(resp.read().decode())
            task_id = data['task_id']
            _log(self.log_box, f'  Enqueued index build → task {task_id}')
            _log(self.log_box, '  Streaming progress below...')
            self._poll_task_progress(task_id)
        except Exception as exc:
            _log(self.log_box, f'  Server enqueue failed ({exc}) — running directly.')
            self._index_build_subprocess()

    def _poll_task_progress(self, task_id: str) -> None:
        """Spawn a daemon thread that polls /tasks/{task_id} and logs progress."""
        import urllib.request, urllib.error

        def _do_poll():
            MAX_WAIT  = 600   # 10 minutes
            elapsed   = 0
            last_pct  = -1

            while elapsed < MAX_WAIT:
                time.sleep(2)
                elapsed += 2
                try:
                    url = f'http://localhost:{REVIEW_PORT}/tasks/{task_id}'
                    with urllib.request.urlopen(url, timeout=5) as resp:
                        task = json.loads(resp.read().decode())
                    status   = task.get('status', '')
                    progress = float(task.get('progress', 0.0))
                    message  = task.get('message', '')
                    pct      = int(progress * 100)

                    if pct != last_pct:
                        last_pct = pct
                        line = f'  [{pct:3d}%] {message}' if message else f'  [{pct:3d}%] {status}'
                        self.log_box.after(0, lambda m=line: _log(self.log_box, m))

                    if status == 'completed':
                        def _on_done():
                            self._stop_busy()
                            self._set_status('Index build complete.')
                        self.log_box.after(0, _on_done)
                        return

                    if status == 'failed':
                        err = task.get('message', 'unknown error')
                        def _on_fail(e=err):
                            self._stop_busy()
                            self._set_status(f'Index build failed — see log for details.')
                            _log(self.log_box, '  ERROR (full traceback):')
                            for line in e.splitlines():
                                _log(self.log_box, f'    {line}')
                        self.log_box.after(0, _on_fail)
                        return

                except Exception:
                    pass  # Server temporarily unavailable — keep polling

            # Timeout after MAX_WAIT seconds
            def _on_timeout():
                self._stop_busy()
                self._set_status('Index build timed out after 10 min. Check logs.')
            self.log_box.after(0, _on_timeout)

        threading.Thread(target=_do_poll, daemon=True).start()

    def _process(self):
        if not self._require_venv():
            return
        if not self._check_index():
            return
        doc = self.doc_var.get().strip()
        if not doc:
            messagebox.showwarning('No document',
                                   'Please use "Browse…" to select the .md document you want to cite.')
            return
        if not os.path.exists(doc):
            messagebox.showerror('Not found', f'File not found:\n{doc}')
            return
        profile = self.profile_var.get()
        profile_path = self._resolve_profile_path(profile)
        cmd = [VENV_PYTHON, 'run.py', 'process', doc, '--yes']
        if profile_path and os.path.exists(profile_path):
            cmd.insert(-1, profile_path)  # insert before --yes
        _log(self.log_box, f'\n--- Processing: {os.path.basename(doc)} ---')
        self._set_status('Processing...')
        self._start_busy()

        doc_name = os.path.basename(doc)

        def after_process():
            self._stop_busy()
            session_id = get_last_session(doc_name)
            if session_id:
                self._set_status('Processing complete. Opening review panel...')
                self.after(2000, self._open_review)
            else:
                self._set_status('No citation triggers found.')
                messagebox.showinfo(
                    'No triggers',
                    'No citation triggers (..) were found in this document.\n\n'
                    'Add ".." after sentences that need citations, then process again.'
                )

        start_review_server(self.log_box)
        self.after(1500, lambda: run_in_pipeline_dir(cmd, self.log_box, on_done=after_process))

    def _open_review(self):
        doc = self.doc_var.get().strip()
        doc_name = os.path.basename(doc) if doc else None
        session_id = get_last_session(doc_name)
        if session_id:
            url = f'http://localhost:{REVIEW_PORT}/review/{session_id}'
            start_review_server(self.log_box)
            self.after(1000, lambda: self._open_url(url))
        else:
            messagebox.showinfo('No session',
                                'No review session found.\nProcess a document first, then open the review panel.')

    def _open_url(self, url):
        """Open a URL in the default browser with error handling."""
        try:
            webbrowser.open(url)
            _log(self.log_box, f'Opened browser: {url}')
        except Exception as e:
            _log(self.log_box, f'Could not open browser: {e}')
            messagebox.showerror('Browser error',
                                 f'Could not open your browser.\n\nManually visit:\n{url}')

    def _export(self):
        if not self._require_venv():
            return
        doc = self.doc_var.get().strip()
        if not doc:
            messagebox.showwarning('No document', 'Please select the document to export.')
            return
        _log(self.log_box, f'\n--- Exporting: {os.path.basename(doc)} ---')
        self._set_status('Exporting...')
        self._start_busy()
        run_in_pipeline_dir(
            [VENV_PYTHON, 'run.py', 'export', doc],
            self.log_box,
            on_done=lambda: (self._stop_busy(), self._set_status('Export complete. Check documents/confirmed/'))
        )

    def _import_tabs(self):
        if not self._require_venv():
            return
        _log(self.log_box, '\n--- Scanning Chrome tabs ---')
        self._set_status('Scanning tabs...')
        self._start_busy()
        start_review_server(self.log_box)

        def after_import():
            self._stop_busy()
            self._set_status('Tab scan complete. Opening import queue...')
            url = f'http://localhost:{REVIEW_PORT}/tab_import_queue'
            self.after(1000, lambda: self._open_url(url))

        run_in_pipeline_dir(
            [VENV_PYTHON, '-m', 'pipeline.tab_importer'],
            self.log_box,
            on_done=after_import
        )

    def _check_index(self):
        """Warn the user if the index hasn't been built yet."""
        index_file = os.path.join(BASE_DIR, 'index', 'faiss_index.bin')
        if not os.path.exists(index_file):
            messagebox.showwarning(
                'Index not built',
                'The source index has not been built yet.\n\n'
                'In Step 1:\n'
                '  1. Browse to your .bib file\n'
                '  2. Browse to your PDFs folder\n'
                '  3. Click "Save & Build Index"\n\n'
                'This only needs to be done once (and again when you add new sources).'
            )
            return False
        return True

    def _on_close(self):
        global server_proc
        try:
            if server_proc and server_proc.poll() is None:
                server_proc.terminate()
        except Exception:
            pass
        self.destroy()

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    os.chdir(BASE_DIR)
    try:
        App().mainloop()
    except Exception as e:
        logger.exception('Unhandled exception in launcher')
        try:
            messagebox.showerror(
                'Fatal Error',
                f'An unexpected error occurred:\n\n{e}\n\n'
                f'Check logs/launcher.log for details.'
            )
        except Exception:
            pass
