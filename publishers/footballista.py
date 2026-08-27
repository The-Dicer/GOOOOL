import re
import logging

logger = logging.getLogger(__name__)


async def add_video_link_to_match(context, match_url: str, video_url: str) -> None:
    """
    Прикрепление ссылки на трансляцию через прямой Footballista REST API:
    POST https://footballista.ru/api/games/{gameId}/set_videos
    """
    logger.info(f"Вставляем видео ({video_url}) по API для: {match_url}")

    # Извлекаем game_id из URL (например, https://footballista.ru/admin/games/549482 -> 549482)
    match_id = None
    m = re.search(r'/games/(\d+)', str(match_url))
    if m:
        match_id = m.group(1)

    if not match_id:
        raise ValueError(f"Не удалось извлечь ID игры из URL: {match_url}")

    page = None
    for p in context.pages:
        if "footballista.ru" in p.url:
            page = p
            break

    if not page:
        page = await context.new_page()
        await page.goto("https://footballista.ru/admin/games")
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(1000)

    api_script = """async ({ gameId, videoUrl }) => {
        async function getIonicToken() {
            return new Promise((resolve) => {
                if (!window.indexedDB) return resolve(null);
                const req = indexedDB.open('_ionicstorage');
                req.onsuccess = (e) => {
                    const db = e.target.result;
                    if (!db.objectStoreNames.contains('_ionickv')) return resolve(null);
                    const tx = db.transaction('_ionickv', 'readonly');
                    const store = tx.objectStore('_ionickv');
                    const getReq = store.get('token');
                    getReq.onsuccess = () => {
                        const val = getReq.result;
                        if (val) return resolve(val.startsWith('Bearer ') ? val : `Bearer ${val}`);
                        resolve(null);
                    };
                    getReq.onerror = () => resolve(null);
                };
                req.onerror = () => resolve(null);
            });
        }

        let token = await getIonicToken();
        if (!token) {
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                const v = localStorage.getItem(k);
                if (v && v.startsWith('eyJ')) { token = `Bearer ${v}`; break; }
            }
        }

        if (!token) {
            return { error: 'Токен авторизации Footballista не найден в браузере' };
        }

        const headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Authorization': token
        };

        // 1. Получаем актуальный объект игры
        const gRes = await fetch(`https://footballista.ru/api/games/${gameId}`, { headers });
        if (!gRes.ok) {
            return { error: `Ошибка получения данных игры: HTTP ${gRes.status}` };
        }

        const game = await gRes.json();
        const cleanVideoUrl = videoUrl.trim();

        // 2. Формируем объект видео (name пустой, type обязателен 'video')
        const newVideoObj = {
            name: "",
            link: cleanVideoUrl,
            type: "video",
            thumbnail: "",
            embed: ""
        };

        // 3. Сохраняем существующие ссылки и добавляем новую (без дубликатов)
        const existingVideos = Array.isArray(game.videos) ? game.videos : [];
        const isDuplicate = existingVideos.some(v => v.link && v.link.trim() === cleanVideoUrl);
        if (!isDuplicate) {
            existingVideos.push(newVideoObj);
        }
        game.videos = existingVideos;
        game.videos.forEach(v => { v.type = "video"; });

        // 4. Отправляем сохранение
        const saveRes = await fetch(`https://footballista.ru/api/games/${game._id}/set_videos`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(game)
        });

        if (!saveRes.ok) {
            const errText = await saveRes.text();
            return { error: `HTTP ${saveRes.status}: ${errText}` };
        }

        return { success: true };
    }"""

    res = await page.evaluate(api_script, {"gameId": match_id, "videoUrl": video_url})
    if res and res.get("error"):
        raise RuntimeError(f"Ошибка прикрепления видео: {res.get('error')}")

    # Перезагружаем открытые вкладки этой игры на Footballista, чтобы пользователь сразу видел обновленный список
    for p in context.pages:
        if f"/games/{match_id}" in p.url:
            try:
                await p.reload()
            except Exception:
                pass

    logger.info(f"✅ Видео успешно прикреплено к матчу {match_id} на Footballista!")
