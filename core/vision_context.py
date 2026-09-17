"""Validated ephemeral image envelope and future shopping handoff."""
import io
from dataclasses import dataclass
from PIL import Image

def validate_frame(data,mime):
    if not data or mime not in ('image/jpeg','image/png'):
        raise ValueError('Captura no válida: no se obtuvo una imagen')
    with Image.open(io.BytesIO(data)) as image:
        if min(image.size)<16:raise ValueError('Captura no válida: dimensiones insuficientes')
        image.verify()
    return data,mime

def vision_intent(text):
    q=str(text).casefold().strip().rstrip('?.!¿¡')
    if any(x in q for x in ('cierra la cámara','cierra la camara','apaga la cámara','apaga la camara')):return 'close'
    if any(x in q for x in ('mi pantalla','mi escritorio','ventana tengo abierta','error que aparece','hay en mi pantalla')):return 'screen'
    if any(x in q for x in ('puedes verme','qué ves','que ves','tengo en la mano','tengo delante','mira este teléfono','mira este telefono')):return 'camera'
    return None  # "lee esto" without a source needs clarification/context, not a guessed camera.

@dataclass(frozen=True)
class ProductObservation:
    brand: str=''
    model: str=''
    confidence: float=0.0
    def next_step(self):
        return 'confirm_identity' if not self.model or self.confidence<.9 else 'search_current_prices'
    @property
    def purchase_allowed(self):return False
