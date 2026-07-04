from pydantic import BaseModel, Field
from typing import Optional
import datetime

class MatchMetadata(BaseModel):
    team_home: str = Field(..., description="Название домашней команды")
    team_away: str = Field(..., description="Название гостевой команды")
    tournament_name: str = Field(..., description="Название турнира или лиги")
    tour_number: str = Field(..., description="Номер тура")
    field_number: Optional[int] = Field(None, description="Номер поля")
    match_date: str = Field(..., description="Дата матча")
    stadium: str = Field("Неизвестно", description="Место проведения матча (стадион)")
    match_url: Optional[str] = Field(None, description="Прямая ссылка на страницу матча")
    logo_home: str = Field("Нет логотипа", description="Ссылка на лого хозяев")
    logo_away: str = Field("Нет логотипа", description="Ссылка на лого гостей")
    abbr_home: str = Field("", description="Сокращение хозяев")
    abbr_away: str = Field("", description="Сокращение гостей")

    @property
    def stream_title(self) -> str:
        # Динамический год (например, '25' или '26')
        current_year = str(datetime.datetime.now().year)[-2:]
        return f"AFL{current_year}. {self.tournament_name}. Day {self.tour_number}. {self.team_home} - {self.team_away}"