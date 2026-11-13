from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class VacanteExtraida:
    portal: str                    # linkedin | computrabajo | elempleo
    id_portal: str                 # id de la vacante dentro del portal
    titulo: str
    url: str                       # URL limpia, sin tokens de seguimiento
    empresa: str | None = None
    ubicacion: str | None = None
    modalidad: str | None = None   # presencial | remoto | hibrido
    salario_texto: str | None = None
    salario_min: int | None = None
    salario_max: int | None = None
    fecha_publicacion: datetime | None = None
    extra: dict = field(default_factory=dict)

    @property
    def id_externo(self) -> str:
        return f"{self.portal}:{self.id_portal}"
