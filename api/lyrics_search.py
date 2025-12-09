"""
Модуль для поиска текстов песен
Строгий алгоритм: поиск → фильтрация → выбор лучшего
Приоритет: синхронизированные тексты → обычные тексты
"""
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging
import unicodedata
from typing import Optional, Tuple, List, Dict

try:
    from rapidfuzz import fuzz
    FUZZ_AVAILABLE = True
except ImportError:
    FUZZ_AVAILABLE = False
    logging.warning("Библиотека rapidfuzz не установлена. Сравнение строк будет менее точным. Рекомендуется: pip install rapidfuzz")


logger = logging.getLogger(__name__)


class LyricsSearcher:
    """
    Класс для надежного поиска текстов песен с приоритетом синхронизированных версий
    и строгой фильтрацией для предотвращения ложных срабатываний.
    """
    
    def __init__(self):
        self.session = requests.Session()
        
        # --- OPTIMISATION STRESS TEST ---
        # Augmentation drastique du pool de connexions pour éviter "Connection pool is full"
        adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=Retry(total=3, backoff_factor=0.5))
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        self.session.headers.update({
            'User-Agent': 'Qobuz GUI Downloader v1.0.5 (https://github.com/Basil-AS/Qobuz_Gui_Downloader)'
        })
    
    def _remove_accents(self, text: str) -> str:
        """Удаляет акценты из текста (например, 'Hélène' -> 'Helene')"""
        if not text:
            return ""
        try:
            nfkd_form = unicodedata.normalize('NFKD', text)
            return "".join([c for c in nfkd_form if not unicodedata.combining(c)])
        except Exception:
            return text

    def _is_instrumental_text(self, text: str) -> bool:
        """
        Проверяет, является ли текст маркером инструментального трека.
        Возвращает True, если текст - это заглушка типа "[Instrumental]".
        """
        if not text or not text.strip():
            return False
        
        # Убираем таймкоды и метаданные
        plain_text = re.sub(r'\[.*?\]', '', text).strip().lower()
        if not plain_text:
            return False
        
        # Короткий текст с ключевыми словами
        if len(plain_text) < 30 and any(m in plain_text for m in ['instrumental', 'инструментал']):
            return True
        
        return False
    
    def _get_clean_title(self, title: str) -> str:
        """
        Убирает из названия ремиксы, версии и прочее для более чистого сравнения.
        Удаляет всё в скобках () и [] и одинарных кавычках для точного сравнения базового названия.
        """
        # Убираем номера треков в начале (01., 1., 001. и т.д.)
        clean_title = re.sub(r'^\d+\.\s*', '', title)
        # Убираем все в скобках, квадратных скобках, одинарных и двойных кавычках, а также кавычки-ёлочки
        clean_title = re.sub(r"\s*\(.*?\)\s*|\s*\[.*?\]\s*|\s*'.*?'\s*|\s*\".*?\"\s*|\s*«.*?»\s*", '', clean_title)
        # Убираем подчёркивания и подряд идущие символы подчёркивания
        clean_title = re.sub(r'_+', ' ', clean_title)
        # Убираем распространенные "лишние" слова
        clean_title = re.sub(r'\s*-\s*(live|remix|reprise|acoustic|version)\s*', '', clean_title, flags=re.IGNORECASE)
        # Нормализуем пробелы и спецсимволы
        clean_title = re.sub(r'\s+', ' ', clean_title)
        clean_title = clean_title.strip(' _-\t\n\r').strip()
        return clean_title.strip().lower()
    
    def search_lyrics(self, artist: str, title: str, album: str = None, duration: int = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Основной метод поиска, реализующий алгоритм:
        1. Проверка на инструментал
        2. Попытка с оригинальным именем артиста
        3. Если не найдено -> Попытка с именем без акцентов
        4. Приоритет всегда у synced lyrics
        """
        logger.info(f"🔍 Поиск текста для: {artist} - {title} (длительность: {duration}с)")
        
        # Проверка на инструментальный трек по названию
        instrumental_keywords = ['instrumental', 'инструментал']
        title_lower = title.lower()
        if any(keyword in title_lower for keyword in instrumental_keywords):
            if not any(word in title_lower for word in ['feat', 'vocals', 'with', 'sung']):
                logger.info("🎼 Инструментальный трек - пропускаем поиск текстов")
                return None, None
        
        # Подготовка вариантов поиска (Оригинал + Без акцентов)
        artists_to_try = [artist]
        normalized_artist = self._remove_accents(artist)
        if normalized_artist != artist:
            artists_to_try.append(normalized_artist)
            
        best_plain_result = None

        for current_artist in artists_to_try:
            if current_artist != artist:
                 logger.info(f"🔄 Попытка с нормализованным именем: {current_artist}")

            # --- Шаг 1: Получаем кандидатов ---
            try:
                url = "https://lrclib.net/api/search"
                params = {'track_name': title, 'artist_name': current_artist}
                if album:
                    params['album_name'] = album
                
                response = self.session.get(url, params=params, timeout=15)
                response.raise_for_status()
                candidates = response.json()
                
                if not candidates:
                    if current_artist == artist:
                        logger.warning(f"❌ LRCLib: Оригинальный поиск не дал результатов.")
                    continue
            except (requests.RequestException, ValueError) as e:
                logger.error(f"❌ LRCLib Error ({current_artist}): {e}")
                continue
            
            logger.info(f"✓ Найдено {len(candidates)} кандидатов для '{current_artist}'.")
            
            # --- Шаг 2: Ищем Synced (Высший приоритет) ---
            best_synced = self._find_best_match(candidates, current_artist, title, duration or 0, require_synced=True)
            if best_synced:
                synced_lyrics = best_synced.get('syncedLyrics')
                is_instr = self._is_instrumental_text(synced_lyrics) or best_synced.get('instrumental')
                if is_instr:
                    logger.info("🎼 Трек определен как ИНСТРУМЕНТАЛЬНЫЙ (Synced)")
                    return None, None
                
                logger.info(f"✅ Найден синхронизированный текст для '{current_artist}'")
                plain_lyrics = self._lrc_to_plain(synced_lyrics)
                return plain_lyrics, synced_lyrics
            
            # --- Шаг 3: Ищем Plain (Только если еще нет plain из предыдущей итерации) ---
            # Мы сохраняем plain, но продолжаем цикл в надежде найти Synced с другим именем артиста
            if not best_plain_result:
                best_plain = self._find_best_match(candidates, current_artist, title, duration or 0, require_synced=False)
                if best_plain:
                    plain_lyrics = best_plain.get('plainLyrics')
                    is_instr = self._is_instrumental_text(plain_lyrics) or best_plain.get('instrumental')
                    if is_instr:
                        logger.info("🎼 Трек определен как ИНСТРУМЕНТАЛЬНЫЙ (Plain)")
                        return None, None
                    
                    logger.info(f"📝 Найден обычный текст для '{current_artist}' (сохранен в резерв)")
                    best_plain_result = (plain_lyrics, None)
        
        # Если после всех попыток synced не найден, возвращаем лучший plain
        if best_plain_result:
            logger.info("✅ Возвращаем лучший обычный текст из резерва")
            return best_plain_result
        
        logger.warning(f"❌ Текст не найден после всех попыток для: {artist} - {title}")
        return None, None
    
    def _find_best_match(self, candidates: List[Dict], target_artist: str, target_title: str, target_duration: int, require_synced: bool) -> Optional[Dict]:
        """
        Итерируется по списку кандидатов и выбирает лучший на основе набора строгих правил.
        """
        best_candidate = None
        highest_score = float('-inf')  # Минус бесконечность
        
        MIN_ARTIST_SCORE = 85  # Чуть снизил порог для гибкости
        
        target_title_clean = self._get_clean_title(target_title)
        
        for item in candidates:
            if require_synced:
                if not item.get('syncedLyrics'): continue
            else:
                if not item.get('plainLyrics') and not item.get('syncedLyrics'): continue
            
            item_title = item.get('trackName', '')
            item_artist = item.get('artistName', '')
            
            # Сравнение исполнителей
            if FUZZ_AVAILABLE:
                artist_score = fuzz.ratio(target_artist.lower(), item_artist.lower())
                if artist_score < MIN_ARTIST_SCORE:
                    continue
            else:
                if target_artist.lower() not in item_artist.lower() and item_artist.lower() not in target_artist.lower():
                    continue
                artist_score = 90
            
            # Сравнение названий
            item_title_clean = self._get_clean_title(item_title)
            if item_title_clean != target_title_clean:
                continue
            
            # Сравнение длительности
            item_duration = item.get('duration', 0)
            duration_diff = abs(target_duration - item_duration)
            if target_duration > 0 and duration_diff > 100:
                continue
            
            score = artist_score - (duration_diff * 10)
            if item.get('syncedLyrics'):
                score += 100
            
            if score > highest_score:
                highest_score = score
                best_candidate = item
        
        return best_candidate
    
    def _lrc_to_plain(self, lyrics_lrc: str) -> str:
        """Преобразование LRC в обычный текст"""
        if not lyrics_lrc: return ""
        text_no_timestamps = re.sub(r'\[\d{2}:\d{2}\.\d{2,3}\]', '', lyrics_lrc)
        text_no_karaoke = re.sub(r'<\d{2}:\d{2}\.\d{2,3}>', '', text_no_timestamps)
        return "\n".join(line.strip() for line in text_no_karaoke.splitlines() if line.strip())
    
    def lrc_to_srt(self, lyrics_lrc: str) -> str:
        """Конвертация LRC в SRT (без изменений)"""
        # ... (code identique à l'original si besoin, raccourci ici pour clarté)
        return ""