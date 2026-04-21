from pydantic import BaseModel, Field
from typing import Optional


class MatchMetadata(BaseModel):
    team_home: str = Field(..., description="Название домашней команды")
    team_away: str = Field(..., description="Название гостевой команды")
    tournament_name: str = Field(..., description="Название турнира или лиги")
    tour_number: int = Field(..., description="Номер тура")
    field_number: Optional[int] = Field(None, description="Номер поля")
    match_date: str = Field(..., description="Дата матча")
    stadium: str = Field("Неизвестно", description="Место проведения матча (стадион)")
    match_url: str = Field(None, description="Прямая ссылка на страницу матча")
    logo_home: str = Field("Нет логотипа", description="Ссылка на лого хозяев")
    logo_away: str = Field("Нет логотипа", description="Ссылка на лого гостей")
    abbr_home: str = Field("", description="Сокращение хозяев")
    abbr_away: str = Field("", description="Сокращение гостей")

    @property
    def stream_title(self) -> str:
        return f"AFL26. {self.tournament_name}. Day {self.tour_number}. {self.team_home} - {self.team_away}"


class MatchMetadataWide(MatchMetadata):
    stream_title_override: Optional[str] = Field(None, description="Кастомный заголовок трансляции")

    @property
    def stream_title(self) -> str:
        if self.stream_title_override:
            return self.stream_title_override
        return super().stream_title
