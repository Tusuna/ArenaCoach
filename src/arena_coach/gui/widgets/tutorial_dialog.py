"""First-run guided walkthrough."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class TutorialDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Arena Coach Tutorial")
        self.resize(860, 620)
        self.pages = QStackedWidget()
        self.pages.addWidget(
            _page_with_sections(
                "Welcome",
                "Arena Coach turns Echo API snapshots into reviewed matches, player histories, and advanced personal stats. "
                "The normal rhythm is capture, process, review, finalize, infer advanced data, then study the results.",
                [
                    (
                        "Main Workflow",
                        [
                            "1. Create your profile and set it active.",
                            "2. Use Test Connection with Echo open.",
                            "3. Start Logging before the match and Stop Logging after it ends.",
                            "4. Press Process Match for Review.",
                            "5. Confirm who you are, link or create players, check teams, and finalize.",
                            "6. Run Infer Selected Match when you want advanced coverage, passing, transition, and shot context.",
                        ],
                    ),
                    (
                        "What the app stores",
                        [
                            "Raw match logs stay on disk.",
                            "Finalized matches feed your personal stats.",
                            "Canonical players and known user IDs are saved locally.",
                            "AFK and low-quality markers stay visible instead of being hidden.",
                        ],
                    ),
                ],
                _flow(["Profile", "Capture", "Review", "Finalize", "Infer", "Study"]),
            )
        )
        self.pages.addWidget(
            _page_with_sections(
                "Live Capture",
                "Live Capture is the starting point when you want a fresh match. You can use the Actions sidebar or the Live Capture tab itself.",
                [
                    (
                        "Buttons you will use most",
                        [
                            "Test Connection: checks whether Arena Coach can see the local Echo API.",
                            "Start Logging: begins recording snapshots into a raw log file.",
                            "Stop Logging: ends capture and writes session metadata.",
                            "Process Match for Review: turns the newest raw log into a reviewable match and opens review.",
                        ],
                    ),
                    (
                        "Advanced log tools",
                        [
                            "Preview Latest Log shows a technical parse preview without saving a match.",
                            "Import Latest Log saves the newest raw log directly into Match History.",
                            "These are mostly for debugging or manual recovery.",
                        ],
                    ),
                ],
                _flow(["Test", "Start", "Stop", "Process"]),
            )
        )
        self.pages.addWidget(_sample_review_page())
        self.pages.addWidget(
            _page_with_sections(
                "Players and Profile",
                "Arena Coach works best when your local player database stays tidy. The Profile tab is about you. The Players tab is about everyone else you want to track consistently.",
                [
                    (
                        "Profile tab",
                        [
                            "Create your local profile with a display name and your main Echo name.",
                            "Set the active profile you want all review and stat work to use.",
                            "Your main Echo name helps Arena Coach suggest which match row is you.",
                        ],
                    ),
                    (
                        "Players tab",
                        [
                            "Search by canonical name or alias.",
                            "Create a new canonical player when you want one identity for many match names.",
                            "Edit the selected player's name or notes.",
                            "Add aliases and known user IDs so future review suggestions get stronger.",
                        ],
                    ),
                ],
                _flow(["Create Profile", "Set Active", "Create Player", "Add Alias"]),
            )
        )
        self.pages.addWidget(
            _page_with_sections(
                "Match History and Advanced Match Data",
                "Match History is where finalized and unfinalized matches live. It also shows the reconstructed scoreboard, round summaries, team rows, and advanced match context.",
                [
                    (
                        "What you can inspect",
                        [
                            "Score, round record, private subtype, quality labels, AFK warnings, and team cards.",
                            "Per-player basic stats plus advanced chips like clears, transition times, misses, and saved shots.",
                            "Event timeline and round summaries when available.",
                        ],
                    ),
                    (
                        "How to get advanced stats",
                        [
                            "A match must be finalized first.",
                            "Then use Infer Selected Match from the Actions sidebar.",
                            "That creates advanced inferred events and player metrics used by Advanced Summary and Compare Players.",
                        ],
                    ),
                ],
                _flow(["Review", "Finalize", "Infer", "Inspect"]),
            )
        )
        self.pages.addWidget(
            _page_with_sections(
                "Stats Preview and Advanced Summary",
                "Stats Preview gives you the high-level picture. Advanced Summary is the deep personal breakdown for the active self player.",
                [
                    (
                        "Stats Preview",
                        [
                            "Shows finalized matches, competitive-eligible counts, win rate, trends, rivals, teammates, and playstyle guesses.",
                            "Use the filters to switch between public, private, tournament, low-quality, and recent-match views.",
                        ],
                    ),
                    (
                        "Advanced Summary",
                        [
                            "Breaks your game into Shooting, Speed, Possession, Offense, Defense, and Passing.",
                            "Use the confidence checkboxes together, not one at a time, to decide which inferred events you trust in the view.",
                            "The radar/grade view is a summary. The cards under it show the exact inputs feeding each category score.",
                        ],
                    ),
                ],
                _flow(["Preview", "Filter", "Read Inputs", "Compare"]),
            )
        )
        self.pages.addWidget(
            _page_with_sections(
                "Compare Players",
                "Compare Players lets you put any two saved players side by side using the same match-type, confidence, and sample filters.",
                [
                    (
                        "How to use it",
                        [
                            "Type in either player box to search the player database quickly.",
                            "Use the same filter mix on both sides when you want fair comparisons.",
                            "The category table shows score deltas. The overview and radar make the gap easier to read at a glance.",
                        ],
                    ),
                    (
                        "Why numbers can change",
                        [
                            "Comparison uses the filters visible on that tab.",
                            "Advanced Summary has its own filter state.",
                            "If two tabs use different confidence levels, match types, or AFK settings, the stats will differ.",
                        ],
                    ),
                ],
                _flow(["Search", "Filter", "Compare", "Interpret"]),
            )
        )
        self.pages.addWidget(
            _page_with_sections(
                "Settings, Exports, and Backups",
                "Settings controls how Arena Coach reaches Echo and how guided review behaves. You can also share data and make safety backups from the app.",
                [
                    (
                        "Data buttons",
                        [
                            "Export My Data creates a shareable zip in exports and opens File Explorer to it.",
                            "Import Shared Data lets you pick another Arena Coach zip and unpacks it into imports.",
                            "Backups create a direct database safety copy before risky changes or updates.",
                        ],
                    ),
                    (
                        "Good habits",
                        [
                            "Only change Echo API host, port, or path if you actually need to.",
                            "Create a backup before major updates or bulk player cleanup.",
                            "Use exports when you want to send your current state back for debugging or planning.",
                        ],
                    ),
                ],
                _flow(["Save Settings", "Export", "Import", "Backup"]),
            )
        )
        self.pages.addWidget(
            _page_with_sections(
                "Ready",
                "You're set. The cleanest test path is still one private match and one public match all the way through capture, review, finalize, and inference.",
                [
                    (
                        "Recommended next check",
                        [
                            "Confirm your profile is active.",
                            "Capture a match.",
                            "Finalize it through Guided Review.",
                            "Run Infer Selected Match.",
                            "Open Advanced Summary and Compare Players to confirm the advanced data looks sane.",
                        ],
                    ),
                    (
                        "Where to look things up later",
                        [
                            "Use Show Tutorial any time from the Actions sidebar.",
                            "Use docs/user_tutorial.md as the longer reference guide.",
                            "Use Debug Logs if something feels off and you need the technical trail.",
                        ],
                    ),
                ],
                _flow(["Capture", "Review", "Finalize", "Infer", "Learn"]),
            )
        )

        self.back_button = QPushButton("Back")
        self.next_button = QPushButton("Next")
        self.skip_button = QPushButton("Skip Tutorial")
        self.finish_button = QPushButton("Start Using Arena Coach")

        self.back_button.clicked.connect(self._back)
        self.next_button.clicked.connect(self._next)
        self.skip_button.clicked.connect(self.accept)
        self.finish_button.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addWidget(self.skip_button)
        buttons.addStretch()
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)
        buttons.addWidget(self.finish_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.pages)
        layout.addLayout(buttons)
        self._sync_buttons()

    def _back(self) -> None:
        self.pages.setCurrentIndex(max(0, self.pages.currentIndex() - 1))
        self._sync_buttons()

    def _next(self) -> None:
        self.pages.setCurrentIndex(min(self.pages.count() - 1, self.pages.currentIndex() + 1))
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        index = self.pages.currentIndex()
        last = index == self.pages.count() - 1
        self.back_button.setEnabled(index > 0)
        self.next_button.setVisible(not last)
        self.finish_button.setVisible(last)


def _page_with_sections(title: str, intro: str, sections: list[tuple[str, list[str]]], flow: QWidget) -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    heading = QLabel(title)
    heading.setStyleSheet("font-size: 20px; font-weight: bold; color: #7ce7ff;")
    body = QLabel(intro)
    body.setWordWrap(True)
    body.setStyleSheet("font-size: 13px;")
    layout.addWidget(heading)
    layout.addWidget(body)
    layout.addWidget(flow)
    for section_title, items in sections:
        layout.addWidget(_section_card(section_title, items))
    layout.addStretch()
    return page


def _section_card(title: str, items: list[str]) -> QWidget:
    box = QGroupBox(title)
    layout = QVBoxLayout(box)
    for item in items:
        label = QLabel(item)
        label.setWordWrap(True)
        layout.addWidget(label)
    return box


def _sample_review_page() -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    heading = QLabel("Guided Review")
    heading.setStyleSheet("font-size: 20px; font-weight: bold; color: #7ce7ff;")
    text = QLabel(
        "Guided Review is the trust step. You confirm who you are, link players to existing identities, create new "
        "players when needed, mark guests, correct teams, and check AFK warnings before the match is finalized."
    )
    text.setWordWrap(True)

    table = QTableWidget(4, 6)
    table.setHorizontalHeaderLabels(["Match Name", "User ID", "Suggested Player", "Team", "Stats", "AFK"])
    rows = [
        ["peef", "1848", "Tusuna - known alias + userid", "orange", "pts=4 a=2 saves=1 stuns=6", "no"],
        ["KnownPlayer", "9981", "KnownPlayer - known userid", "orange", "pts=6 a=1 saves=0 stuns=2", "no"],
        ["StillPlayer", "7712", "no match found", "blue", "pts=0 a=0 saves=0 stuns=0", "AFK?"],
        ["NewName", "5544", "no match found", "blue", "pts=2 a=0 saves=1 stuns=4", "no"],
    ]
    for row_index, row in enumerate(rows):
        for column, value in enumerate(row):
            table.setItem(row_index, column, QTableWidgetItem(value))
    table.resizeColumnsToContents()

    card = _section_card(
        "What each review choice means",
        [
            "Existing Player: link this match row to someone already in your local database.",
            "Create New Player: make a new canonical identity from this match row.",
            "Mark As Me: sets your self player for this match. There must be exactly one.",
            "Guest/Unknown: keep the row for history, but do not merge it into a saved canonical player yet.",
            "AFK: leave this on if the player was not meaningfully participating.",
        ],
    )

    layout.addWidget(heading)
    layout.addWidget(text)
    layout.addWidget(_flow(["Summary", "Identify Me", "Assign Players", "Teams", "Finalize"]))
    layout.addWidget(table)
    layout.addWidget(card)
    return page


def _flow(labels: list[str]) -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    for index, label in enumerate(labels):
        button = QPushButton(label)
        button.setEnabled(False)
        layout.addWidget(button)
        if index < len(labels) - 1:
            arrow = QLabel("->")
            arrow.setStyleSheet("color: #ffb347; font-weight: bold;")
            layout.addWidget(arrow)
    layout.addStretch()
    return widget
