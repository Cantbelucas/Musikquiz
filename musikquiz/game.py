"""Quiz-regler. Kender kun til TrackSource-abstraktionen, ikke til filer/Spotify."""
from __future__ import annotations

import json
import random
import secrets
from dataclasses import asdict, dataclass, field

from .db import transaction
from .sources import Criteria, TrackSource

# (sekunder afspillet, point ved korrekt gæt på dette trin)
STAGES: tuple[tuple[int, int], ...] = ((1, 10), (2, 6), (5, 3), (10, 1))

PLAYING, REVEALED, FINISHED = "playing", "revealed", "finished"


class GameError(Exception):
    """Ugyldig handling i spillet (fx gæt efter svaret er vist)."""


class GameConflict(Exception):
    """Spillet blev ændret af en anden forespørgsel imens — prøv igen."""


@dataclass
class Option:
    ref: str
    title: str
    artist: str


@dataclass
class SongResult:
    ref: str
    title: str
    artist: str
    points: int
    solved: bool
    seconds: int | None  # sekund-trin hvor den blev gættet, None = ikke gættet


@dataclass
class Game:
    id: str
    category: str
    value: str | None
    difficulty: str
    total: int
    index: int = 0
    stage: int = 0
    status: str = PLAYING
    score: int = 0
    current_ref: str = ""
    start_at: float = 0.0
    options: list[Option] = field(default_factory=list)
    wrong: list[int] = field(default_factory=list)
    played: list[str] = field(default_factory=list)
    results: list[SongResult] = field(default_factory=list)
    highscore_saved: bool = False

    # --- serialisering ----------------------------------------------------
    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "Game":
        data = json.loads(raw)
        data["options"] = [Option(**o) for o in data["options"]]
        data["results"] = [SongResult(**r) for r in data["results"]]
        return cls(**data)

    @property
    def criteria(self) -> Criteria:
        return Criteria(self.category, self.value, self.difficulty)

    @property
    def max_score(self) -> int:
        return self.total * STAGES[0][1]

    @property
    def seconds(self) -> int:
        return STAGES[self.stage][0]

    # --- regler -------------------------------------------------------------
    def _load_song(self, source: TrackSource, choices: int) -> bool:
        track = source.random_track(self.criteria, exclude=set(self.played))
        if track is None:
            return False
        others = source.distractors(track, self.criteria, max(choices - 1, 0))
        opts = [Option(t.ref, t.title, t.artist) for t in [track, *others]]
        random.shuffle(opts)
        self.current_ref = track.ref
        self.start_at = track.start_at
        self.options = opts
        self.wrong = []
        self.stage = 0
        self.status = PLAYING
        self.played.append(track.ref)
        return True

    def _reveal(self, points: int, solved: bool) -> None:
        answer = next(o for o in self.options if o.ref == self.current_ref)
        self.score += points
        self.results.append(
            SongResult(
                ref=answer.ref,
                title=answer.title,
                artist=answer.artist,
                points=points,
                solved=solved,
                seconds=self.seconds if solved else None,
            )
        )
        self.status = REVEALED

    def _require(self, status: str) -> None:
        if self.status != status:
            raise GameError("Handlingen er ikke mulig lige nu.")

    def guess(self, option_index: int) -> bool:
        self._require(PLAYING)
        if not 0 <= option_index < len(self.options) or option_index in self.wrong:
            raise GameError("Ugyldigt svar.")
        if self.options[option_index].ref == self.current_ref:
            self._reveal(STAGES[self.stage][1], solved=True)
            return True
        self.wrong.append(option_index)
        self._advance()
        return False

    def skip(self) -> None:
        self._require(PLAYING)
        self._advance()

    def _advance(self) -> None:
        if self.stage + 1 < len(STAGES):
            self.stage += 1
        else:
            self._reveal(0, solved=False)

    def next_song(self, source: TrackSource, choices: int) -> None:
        self._require(REVEALED)
        if self.index + 1 >= self.total or not self._next_loaded(source, choices):
            self.total = len(self.results)
            self.status = FINISHED
            self.options = []
            self.current_ref = ""

    def _next_loaded(self, source: TrackSource, choices: int) -> bool:
        self.index += 1
        if self._load_song(source, choices):
            return True
        self.index -= 1
        return False

    # --- til klienten ------------------------------------------------------
    def public_state(self) -> dict:
        """Det klienten må se. Det rigtige svar afsløres først når det er vist."""
        state = {
            "status": self.status,
            "index": self.index,
            "total": self.total,
            "score": self.score,
            "max_score": self.max_score,
            "stage": self.stage,
            "stages": [{"seconds": s, "points": p} for s, p in STAGES],
            "category": self.category,
            "value": self.value,
            "difficulty": self.difficulty,
        }
        if self.status in (PLAYING, REVEALED):
            state["start_at"] = self.start_at
            state["options"] = [
                {"id": i, "title": o.title, "artist": o.artist, "wrong": i in self.wrong}
                for i, o in enumerate(self.options)
            ]
        if self.status == REVEALED and self.results:
            last = self.results[-1]
            state["reveal"] = asdict(last)
            state["answer_id"] = next(i for i, o in enumerate(self.options) if o.ref == self.current_ref)
            state["is_last"] = self.index + 1 >= self.total
        if self.status == FINISHED:
            state["results"] = [asdict(r) for r in self.results]
            state["highscore_saved"] = self.highscore_saved
        return state


# --- persistens ------------------------------------------------------------
def create_game(db, source: TrackSource, criteria: Criteria, round_length: int, choices: int) -> Game | None:
    available = source.count(criteria)
    if available == 0:
        return None
    game = Game(
        id=secrets.token_urlsafe(18),
        category=criteria.category,
        value=criteria.value,
        difficulty=criteria.difficulty,
        total=min(round_length, available),
    )
    if not game._load_song(source, choices):
        return None
    with transaction(db):
        db.execute("DELETE FROM games WHERE updated_at < datetime('now', '-7 days')")
        db.execute("INSERT INTO games (id, state) VALUES (?, ?)", (game.id, game.to_json()))
    return game


def load_game(db, game_id: str) -> Game | None:
    row = db.execute("SELECT state FROM games WHERE id = ?", (game_id,)).fetchone()
    return Game.from_json(row["state"]) if row else None


class game_update:
    """`with game_update(db, id) as game:` — indlæs, mutér, gem.

    Modsat `locked_game` holdes der INGEN skrivelås, mens blokken kører. Det er
    nødvendigt for "næste sang", som kan kalde ud over netværket (tjek af preview);
    med en lås ville alle andre spilleres klik vente imens. I stedet gemmes der til
    sidst i en kort transaktion, der kun lykkes, hvis runden ikke er ændret i
    mellemtiden — ellers rejses GameConflict, og klienten kan prøve igen.
    """

    def __init__(self, db, game_id: str):
        self.db, self.game_id, self.game, self.version = db, game_id, None, None

    def __enter__(self) -> Game | None:
        row = self.db.execute("SELECT state, version FROM games WHERE id = ?", (self.game_id,)).fetchone()
        if row is None:
            return None
        self.version = row["version"]
        self.game = Game.from_json(row["state"])
        return self.game

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None or self.game is None:
            return False
        with transaction(self.db):
            changed = self.db.execute(
                """UPDATE games SET state = ?, finished = ?, version = version + 1,
                   updated_at = datetime('now') WHERE id = ? AND version = ?""",
                (self.game.to_json(), int(self.game.status == FINISHED), self.game.id, self.version),
            ).rowcount
        if not changed:
            raise GameConflict("Runden blev opdateret et andet sted. Prøv igen.")
        return False


class locked_game:
    """`with locked_game(db, id) as game:` — låser, indlæser og gemmer spillet atomisk."""

    def __init__(self, db, game_id: str):
        self.db, self.game_id, self.tx = db, game_id, transaction(db)

    def __enter__(self) -> Game | None:
        self.tx.__enter__()
        self.game = load_game(self.db, self.game_id)
        return self.game

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None and self.game is not None:
            self.db.execute(
                "UPDATE games SET state = ?, finished = ?, updated_at = datetime('now') WHERE id = ?",
                (self.game.to_json(), int(self.game.status == FINISHED), self.game.id),
            )
        return self.tx.__exit__(exc_type, exc, tb)
