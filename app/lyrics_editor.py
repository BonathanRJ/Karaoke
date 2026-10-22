"""Editor de letra: buscar letra sincronizada, sincronizar letra colada,
separar voz/instrumental e revisar tocando a música."""
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from .database import update_lyrics_path
from .genius_lyrics import correct_with_genius, correction_summary
from .lrc import format_lrc, parse_editor_lines
from .preparation import GeniusLyricsWorker, OnlineLyricsWorker
from .tasks import run_task
from .workflows import PreparationFlow, alignment_quality, lyrics_path_for, online_offset_message


def _existing_file(path):
    return path if path and Path(path).exists() else None


class LyricsEditor(QDialog):
    def __init__(self, song, player, parent=None):
        super().__init__(parent)

        self.song = song
        self.player = player
        self._just_separated = False

        self.flow = PreparationFlow(song, self, lyrics_hint=self._plain_lyrics)
        self.flow.stems_ready.connect(self._on_stems_ready)
        self.flow.separation_failed.connect(self._on_separation_failed)
        self.flow.lyrics_synced.connect(self._on_lyrics_synced)
        self.flow.lyrics_skipped.connect(self._on_lyrics_skipped)
        self.flow.sync_failed.connect(self._on_sync_failed)

        self.setWindowTitle(f"Editor de letra — {song['title']}")
        self.resize(850, 650)

        layout = QVBoxLayout(self)

        info = QLabel(
            "Use 'Buscar letra sincronizada' para baixar uma letra já sincronizada (LRCLIB/NetEase), "
            "'Conferir texto no Genius' para corrigir o texto mantendo os tempos, "
            "ou cole a letra e use 'Sincronizar letra colada'."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("Cole ou digite a letra aqui, uma linha por linha...")
        self.text_edit.setStyleSheet("QTextEdit { font-size: 17px; }")
        layout.addWidget(self.text_edit)

        controls = QHBoxLayout()
        self.action_buttons = []
        for text, tooltip, slot in (
            ("Buscar letra sincronizada",
             "Procura no LRCLIB e no NetEase uma letra já sincronizada por outras pessoas, "
             "confere o texto no Genius e ajusta o atraso usando o vocal separado, quando existir.",
             self.search_online_lyrics),
            ("Conferir texto no Genius",
             "Corrige o texto da letra aberta com a versão do Genius, mantendo os tempos de cada linha.",
             self.check_text_with_genius),
            ("Sincronizar letra colada",
             "Usa o vocal separado somente para localizar os tempos. O texto da letra é preservado.",
             self.synchronize_pasted_lyrics),
            ("Preparar voz/instrumental",
             "Separa a música original em vocal e instrumental. Depois a letra correta será obrigatória.",
             self.prepare_voice_and_instrumental),
            ("Remover tempos", "", self.remove_times),
        ):
            button = QPushButton(text)
            button.setToolTip(tooltip)
            button.clicked.connect(slot)
            controls.addWidget(button)
            self.action_buttons.append(button)
        controls.addStretch()
        self.clock_label = QLabel("Tempo atual: 00:00.00")
        controls.addWidget(self.clock_label)
        layout.addLayout(controls)

        playback = QHBoxLayout()
        back_button = QPushButton("-5 s")
        back_button.clicked.connect(lambda: self.seek_relative(-5000))
        playback.addWidget(back_button)
        self.play_pause_button = QPushButton("Play")
        self.play_pause_button.clicked.connect(self.player.toggle)
        playback.addWidget(self.play_pause_button)
        forward_button = QPushButton("+5 s")
        forward_button.clicked.connect(lambda: self.seek_relative(5000))
        playback.addWidget(forward_button)
        playback.addStretch()
        layout.addLayout(playback)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.player.state_changed.connect(self.update_play_button)
        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(50)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start()
        # Ao fechar, o editor deixa de acompanhar o player (antes, cada editor já
        # fechado continuava ligado a ele). O objeto continua vivo para que uma
        # separação ou sincronização em andamento possa terminar.
        self.finished.connect(self._disconnect_player)

        self.load_existing()
        self.update_clock()
        self.update_play_button("playing" if self.player.is_playing() else "paused")

    # --- arquivo -------------------------------------------------------------
    def load_existing(self):
        path = _existing_file(self.song["lyrics_path"])
        if not path:
            return
        try:
            self.text_edit.setPlainText(Path(path).read_text(encoding="utf-8"))
        except OSError as error:
            QMessageBox.warning(self, "Erro", f"Não foi possível ler a letra:\n{error}")

    def save(self):
        path = self.song["lyrics_path"] or str(lyrics_path_for(self.song["audio_path"]))
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(self.text_edit.toPlainText(), encoding="utf-8")
        except OSError as error:
            QMessageBox.critical(self, "Erro ao salvar", f"Não foi possível salvar a letra:\n{error}")
            return
        update_lyrics_path(self.song["id"], path)
        self.accept()

    def remove_times(self):
        rows = parse_editor_lines(self.text_edit.toPlainText())
        for row in rows:
            row["time"] = None
            row["words"] = []
        self.text_edit.setPlainText(format_lrc(rows))

    def _plain_lyrics(self):
        rows = parse_editor_lines(self.text_edit.toPlainText())
        return "\n".join(row["text"] for row in rows if row.get("text", "").strip())

    # --- reprodução ----------------------------------------------------------
    def update_clock(self):
        milliseconds = self.player.position()
        minutes, seconds = milliseconds // 60000, (milliseconds // 1000) % 60
        self.clock_label.setText(f"Tempo atual: {minutes:02d}:{seconds:02d}.{(milliseconds % 1000) // 10:02d}")

    def seek_relative(self, delta_ms):
        duration = self.player.media.duration()
        position = self.player.position() + delta_ms
        if duration > 0:
            position = min(position, duration)
        self.player.set_position(max(0, position))
        self.update_clock()

    def update_play_button(self, state):
        self.play_pause_button.setText("Pause" if state == "playing" else "Play")

    def _disconnect_player(self):
        self.clock_timer.stop()
        try:
            self.player.state_changed.disconnect(self.update_play_button)
        except (RuntimeError, TypeError):
            pass

    # --- ações demoradas -----------------------------------------------------
    def _set_busy(self, busy):
        for button in self.action_buttons:
            button.setEnabled(not busy)

    def search_online_lyrics(self):
        self._set_busy(True)
        run_task(
            self,
            OnlineLyricsWorker(
                self.song.get("artist") or "",
                self.song.get("title") or "",
                self.song.get("audio_path"),
                _existing_file(self.song.get("vocals_path")),
            ),
            self._on_online_lyrics_found,
            self._on_online_lyrics_failed,
            title="Buscar letra sincronizada",
            text="Procurando letra sincronizada no LRCLIB e no NetEase...",
            busy=True,
        )

    def _on_online_lyrics_found(self, content, source, offset_ms, offset_checked, text_note):
        self._set_busy(False)
        if self.text_edit.toPlainText().strip():
            answer = QMessageBox.question(
                self,
                "Letra encontrada",
                f"Uma letra sincronizada foi encontrada no {source}.\n\n"
                "Substituir o conteúdo atual do editor por ela?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.text_edit.setPlainText(content)
        QMessageBox.information(
            self,
            "Letra encontrada",
            f"Letra sincronizada encontrada no {source}.\n\n"
            f"{text_note}\n\n"
            f"{online_offset_message(offset_ms, offset_checked)}\n\n"
            "Revise tocando a música e clique em Salvar.",
        )

    def _on_online_lyrics_failed(self, message):
        self._set_busy(False)
        QMessageBox.warning(self, "Letra não encontrada", message)

    def check_text_with_genius(self):
        self._set_busy(True)
        run_task(
            self,
            GeniusLyricsWorker(self.song.get("artist") or "", self.song.get("title") or ""),
            self._on_genius_text,
            self._on_genius_failed,
            title="Genius",
            text="Buscando a letra no Genius...",
            busy=True,
        )

    def _on_genius_text(self, genius_lines):
        self._set_busy(False)
        rows = parse_editor_lines(self.text_edit.toPlainText())
        # Linhas com tempo e sem texto são pausas instrumentais: também são mantidas.
        timed = [{"time": row["time"], "text": row["text"].strip(), "words": []}
                 for row in rows if row["time"] is not None]
        rows = [row for row in rows if row["text"].strip()]

        if not any(line["text"] for line in timed):
            # Sem tempos não há o que preservar: a letra do Genius substitui o texto.
            if rows:
                answer = QMessageBox.question(
                    self, "Genius", "A letra aberta não tem tempos. Substituir pelo texto do Genius?"
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            self.text_edit.setPlainText("\n".join(genius_lines))
            QMessageBox.information(
                self,
                "Genius",
                "O texto do Genius não tem tempos. Para sincronizá-lo, use 'Sincronizar letra colada' "
                "(precisa do vocal separado).",
            )
            return
        if any(row["time"] is None for row in rows):
            QMessageBox.warning(
                self,
                "Genius",
                "Algumas linhas estão sem tempo. Para conferir o texto, todas as linhas precisam ter tempo.",
            )
            return

        corrected = correct_with_genius(timed, genius_lines)
        if corrected is None:
            QMessageBox.warning(
                self,
                "Genius",
                "A letra do Genius parece ser de outra versão da música; nada foi alterado.",
            )
            return
        lines, changed, inserted = corrected
        if changed or inserted:
            self.text_edit.setPlainText(format_lrc(lines))
        QMessageBox.information(
            self,
            "Genius",
            correction_summary(changed, inserted) + ("\n\nRevise e clique em Salvar." if changed or inserted else ""),
        )

    def _on_genius_failed(self, message):
        self._set_busy(False)
        QMessageBox.warning(self, "Genius", message)

    def synchronize_pasted_lyrics(self):
        if not _existing_file(self.song.get("vocals_path")):
            QMessageBox.information(
                self,
                "Prepare a música primeiro",
                "Primeiro separe a música em vocal e instrumental usando 'Preparar voz/instrumental'.",
            )
            return
        self._just_separated = False
        self._set_busy(True)
        self.flow.ask_lyrics_and_sync()

    def prepare_voice_and_instrumental(self):
        if _existing_file(self.song.get("vocals_path")):
            self.synchronize_pasted_lyrics()
            return

        source = self.song.get("source_audio_path") or self.song.get("audio_path")
        if not _existing_file(source):
            QMessageBox.warning(self, "Arquivo não encontrado", "A música original com vocal não foi localizada.")
            return
        answer = QMessageBox.question(
            self,
            "Preparar voz e instrumental",
            "O sistema vai separar a música em vocal e instrumental. Depois disso você precisará "
            "colar a letra correta para continuar. Deseja iniciar?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self.flow.separate()

    def _on_stems_ready(self, instrumental_path, _vocals_path):
        self._just_separated = True
        self.player.load(instrumental_path)

    def _on_separation_failed(self, message):
        self._set_busy(False)
        QMessageBox.critical(self, "Erro ao separar música", message)

    def _on_lyrics_synced(self, lyrics_path, confidence):
        self._set_busy(False)
        try:
            self.text_edit.setPlainText(Path(lyrics_path).read_text(encoding="utf-8"))
        except OSError as error:
            QMessageBox.warning(self, "Letra sincronizada", f"O LRC foi criado, mas não pôde ser aberto:\n{error}")
            return
        QMessageBox.information(
            self,
            "Letra sincronizada",
            f"A letra que você colou foi preservada e sincronizada.\n\n{alignment_quality(confidence)}",
        )

    def _on_lyrics_skipped(self):
        self._set_busy(False)
        if self._just_separated:
            QMessageBox.information(
                self,
                "Separação concluída",
                "Vocal e instrumental foram gerados. A letra continua pendente e pode ser colada "
                "depois pelo botão 'Sincronizar letra colada'.",
            )

    def _on_sync_failed(self, message):
        self._set_busy(False)
        QMessageBox.critical(self, "Erro ao sincronizar letra", message)
