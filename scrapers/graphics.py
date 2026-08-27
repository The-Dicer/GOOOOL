import re
import os
import base64
import logging
import asyncio
from pathlib import Path
from typing import Optional, Dict
from models import MatchMetadata

logger = logging.getLogger(__name__)


async def _run_graphics_flow(graphics_page, match: MatchMetadata, pattern_mode: str, league: str,
                             default_color: int, stadium_colors: Dict[str, str],
                             color_position: int, pattern_position: int, download_path: Path) -> str:
    """Выполняет один цикл выбора настроек и скачивания обложки."""
    # Сброс открытых модалок
    try:
        await graphics_page.keyboard.press("Escape")
        await graphics_page.wait_for_timeout(200)
    except Exception:
        pass

    # 1. ПРОВЕРКА / ВЫБОР ТУРНИРА
    target_tournament = match.tournament_name
    season_input = graphics_page.locator("input[placeholder='Select season']").first
    current_season_val = ""
    if await season_input.count() > 0:
        try:
            current_season_val = await season_input.input_value()
        except Exception:
            pass

    if target_tournament.lower() in current_season_val.lower() and current_season_val.strip():
        logger.info(f"Турнир '{target_tournament}' уже активен ({current_season_val}).")
    else:
        logger.info(f"Выбор турнира: {target_tournament} ({league})...")
        season_wrapper = season_input.locator("xpath=..")
        chevron = season_wrapper.locator(".mantine-Input-rightSection")
        if await chevron.count() > 0:
            await chevron.click(force=True)
        else:
            await season_input.click(force=True)

        modal_inner = graphics_page.locator(".mantine-Modal-inner")
        await modal_inner.wait_for(state="visible", timeout=6000)

        def normalize_name(text: str) -> str:
            if not text:
                return ""
            t = text.lower()
            t = re.sub(r'["\'«»“”„`]', '', t)
            t = re.sub(r'[\(\)\[\]\{\}\-_,.]', ' ', t)
            return re.sub(r'\s+', ' ', t).strip()

        translit_map = {
            'euroleague': 'евролига',
            'champions league': 'лига чемпионов',
            'champions': 'чемпионов',
            'conference league': 'лига конференций',
            'conference': 'конференций',
            'south': 'юг',
            'north': 'север',
            'west': 'запад',
            'east': 'восток',
            'cup': 'кубок',
            'premier': 'премьер',
            'superleague': 'суперлига',
            'division': 'дивизион',
            'first': '1',
            'second': '2',
            'third': '3',
            'b': 'б',
            'a': 'а',
            'c': 'с',
            'd': 'д'
        }

        norm_target = normalize_name(target_tournament)
        search_variants = [norm_target]

        # 1. Вариант транслита в кириллицу
        t_rus = norm_target
        for eng, rus in translit_map.items():
            t_rus = re.sub(rf'\b{re.escape(eng)}\b', rus, t_rus)
        if t_rus != norm_target and t_rus not in search_variants:
            search_variants.append(t_rus)

        # 2. Вариант транслита в латиницу
        t_eng = norm_target
        for eng, rus in translit_map.items():
            t_eng = re.sub(rf'\b{re.escape(rus)}\b', eng, t_eng)
        if t_eng != norm_target and t_eng not in search_variants:
            search_variants.append(t_eng)

        country_blocks = await modal_inner.locator(".IgrSeasonSelect_country__letoO").all()
        found_league = None
        available_champs = []

        # Собираем все турниры с их родительскими странами/категориями
        candidates = []
        for c_block in country_blocks:
            c_name_elem = c_block.locator(".IgrSeasonSelect_countryName__Er6s8")
            c_name = (await c_name_elem.inner_text()).strip() if await c_name_elem.count() > 0 else ""
            c_name_norm = normalize_name(c_name)

            champ_elems = await c_block.locator(".IgrSeasonSelect_champ__r06TO").all()
            for ch in champ_elems:
                # Берем только название турнира (без списка сезонов в скобках)
                title_div = ch.locator("div.IgrSeasonSelect_champName__BVzC6 > div").first
                if await title_div.count() > 0:
                    ch_title = (await title_div.inner_text()).strip()
                else:
                    ch_title = (await ch.locator("div.IgrSeasonSelect_champName__BVzC6").inner_text()).strip()
                
                ch_title_norm = normalize_name(ch_title)
                combined_norm = f"{c_name_norm} {ch_title_norm}".strip()
                candidates.append((ch, c_name, ch_title, ch_title_norm, combined_norm))
                available_champs.append(f"{c_name} -> {ch_title}")

        # Поиск лучшего совпадения по приоритету
        # Приоритет 1: Точное совпадение ch_title или combined_norm с одним из вариантов
        for ch, c_name, ch_title, ch_title_norm, combined_norm in candidates:
            for v in search_variants:
                if v == ch_title_norm or v == combined_norm:
                    found_league = (ch, c_name, ch_title)
                    break
            if found_league:
                break

        # Приоритет 2: Все слова из целевого турнира присутствуют в названии или комбинации
        if not found_league:
            for ch, c_name, ch_title, ch_title_norm, combined_norm in candidates:
                for v in search_variants:
                    words = v.split()
                    if words and all(re.search(rf'\b{re.escape(w)}\b', f"{combined_norm} {ch_title_norm}") for w in words):
                        found_league = (ch, c_name, ch_title)
                        break
                if found_league:
                    break

        # Приоритет 3: Подстрока
        if not found_league:
            for ch, c_name, ch_title, ch_title_norm, combined_norm in candidates:
                for v in search_variants:
                    if v in combined_norm or v in ch_title_norm:
                        found_league = (ch, c_name, ch_title)
                        break
                if found_league:
                    break

        if not found_league:
            logger.error(f"❌ Турнир '{target_tournament}' не найден! Доступные турниры на сайте: {available_champs}")
            raise ValueError(f"Турнир '{target_tournament}' не найден среди доступных на AFL Graphics: {available_champs}")

        target_ch_elem, found_c_name, found_ch_title = found_league
        logger.info(f"Найден подходящий турнир: '{found_ch_title}' (категория '{found_c_name}')")

        # Извлекаем год матча (например, из '23.08.2026 14:00' -> '2026')
        match_year = ""
        y_match = re.search(r'\b(20\d\d)\b', match.match_date)
        if y_match:
            match_year = y_match.group(1)

        year_buttons = target_ch_elem.locator("div.IgrSeasonSelect_season__AUXMG")
        years_count = await year_buttons.count()
        selected_season_btn = None

        if years_count > 0:
            if match_year:
                for yi in range(years_count):
                    btn = year_buttons.nth(yi)
                    if (await btn.inner_text()).strip() == match_year:
                        selected_season_btn = btn
                        break

            if not selected_season_btn:
                selected_season_btn = year_buttons.nth(years_count - 1)

            season_txt = await selected_season_btn.inner_text()
            logger.info(f"Выбран сезон: {season_txt}")
            await selected_season_btn.click(force=True)

        await modal_inner.wait_for(state="hidden", timeout=6000)
        logger.info("Турнир успешно выбран.")

    # Ждем появления элементов управления сезоном (игры и цвета)
    try:
        await graphics_page.locator("input[placeholder='Select game'], .IgrSchemaSelect_select__dlKN6, [class*='schemaProvider']").first.wait_for(state="attached", timeout=8000)
        await graphics_page.wait_for_timeout(300)
    except Exception as e:
        logger.warning(f"Ожидание компонентов сезона: {e}")

    # 2. ВЫБОР Cover2
    cover_wrapper = graphics_page.locator(".mantine-Input-wrapper").nth(2)
    cover_input = cover_wrapper.locator("input")
    current_cover = ""
    try:
        current_cover = await cover_input.input_value()
    except Exception:
        pass

    if "Cover2" not in current_cover:
        logger.info(f"Переключение на Cover2 (текущий: {current_cover})...")
        cover_chevron = cover_wrapper.locator(".mantine-Input-rightSection")
        if await cover_chevron.count() > 0:
            await cover_chevron.click(force=True)
        else:
            await cover_input.click(force=True)
        await graphics_page.wait_for_timeout(200)
        await graphics_page.get_by_role("option", name="Cover2", exact=True).click(force=True)
        await graphics_page.wait_for_timeout(300)

    # 3. ЦВЕТ СТАДИОНА И ПАТТЕРН
    stadium_name = match.stadium or ""
    stadium_lower = stadium_name.lower()
    color_pos = int(default_color)

    for keyword, pos in stadium_colors.items():
        if keyword.lower() in stadium_lower:
            try:
                color_pos = int(pos)
                logger.info(f"Найден стадион '{keyword}' в базе: назначаем цвет № {color_pos}")
            except ValueError:
                pass
            break

    if pattern_mode in ["Паттерн 1", "1"]:
        pattern_pos = 1
    elif pattern_mode in ["Паттерн 2", "2"]:
        pattern_pos = 2
    else:
        if "поле 2" in stadium_lower or "дальнее" in stadium_lower or "2" in stadium_lower:
            pattern_pos = 2
        else:
            pattern_pos = pattern_position

    logger.info(f"Применяем для '{stadium_name}': цвет {color_pos}, паттерн {pattern_pos}")

    color_script = f"""async () => {{
        // Ищем триггер открытия селектора цветов
        let trigger = document.querySelector('.IgrSchemaSelect_select__dlKN6, [class*="schemaProvider"] [class*="select"], .IgrSchemaSelect_container__lLhtL, [class*="colorsContainer"]');
        
        // Если селектор еще не открыт, кликаем по триггеру
        let root = document.querySelector('.IgrSchemaSelect_colorsSelect___M4an, [class*="colorsSelect"]');
        if (!root && trigger) {{
            trigger.click();
            for (let t = 0; t < 20; t++) {{
                await new Promise(r => setTimeout(r, 100));
                root = document.querySelector('.IgrSchemaSelect_colorsSelect___M4an, [class*="colorsSelect"]');
                if (root) break;
            }}
        }}

        if (!root) return {{ error: 'Блок выбора цветов не найден на странице' }};

        // 1. Выбираем цвет
        const colorItems = Array.from(root.querySelectorAll('.IgrSchemaSelect_colorsContainerSelect__pRdYU, [class*="colorsContainerSelect"]'));
        const targetColorIdx = Math.max(0, Math.min({color_pos} - 1, colorItems.length - 1));
        const chosenColor = colorItems[targetColorIdx];

        if (chosenColor) {{
            chosenColor.click();
        }}

        await new Promise(r => setTimeout(r, 150));

        // 2. Выбираем паттерн
        const patternItems = Array.from(document.querySelectorAll('.IgrSchemaSelect_patternSample__xbYqJ, [class*="patternSample"]'));
        const targetPatternIdx = Math.max(0, Math.min({pattern_pos} - 1, patternItems.length - 1));
        if (patternItems.length > 0 && patternItems[targetPatternIdx]) {{
            patternItems[targetPatternIdx].click();
        }}

        await new Promise(r => setTimeout(r, 150));

        // 3. Закрываем модалку
        const closeBtn = document.querySelector('.mantine-Modal-close');
        if (closeBtn) {{
            closeBtn.click();
        }} else {{
            const modal = document.querySelector('.mantine-Modal-inner');
            if (modal) {{
                const ev = new KeyboardEvent('keydown', {{ key: 'Escape', code: 'Escape', keyCode: 27, bubbles: true }});
                document.dispatchEvent(ev);
            }}
        }}

        return {{ success: true, color: targetColorIdx + 1, pattern: targetPatternIdx + 1 }};
    }}"""

    color_res = await graphics_page.evaluate(color_script)
    if color_res and color_res.get("success"):
        logger.info(f"✅ Успешно установлен цвет № {color_res.get('color')}, паттерн № {color_res.get('pattern')}")
    elif color_res and color_res.get("error"):
        logger.warning(f"Предупреждение при выборе цвета: {color_res.get('error')}")
    await graphics_page.wait_for_timeout(250)

    # 4. ВЫБОР МАТЧА
    logger.info(f"Ищем матч: {match.team_home} - {match.team_away} (тур {match.tour_number})")
    game_input = graphics_page.locator("input[placeholder='Select game']").first
    await game_input.wait_for(state="visible", timeout=6000)
    await game_input.click(force=True)
    await game_input.fill(str(match.tour_number))
    await graphics_page.wait_for_timeout(400)

    safe_home = re.sub(r'\s+', ' ', match.team_home.replace('ТП', '').strip())
    safe_away = re.sub(r'\s+', ' ', match.team_away.replace('ТП', '').strip())
    search_pattern = re.compile(f"{re.escape(safe_home)}.*{re.escape(safe_away)}", re.IGNORECASE)

    game_option = graphics_page.get_by_role("option", name=search_pattern).first
    try:
        await game_option.wait_for(state="visible", timeout=2500)
        await game_option.click(force=True)
        logger.info("Матч выбран из списка!")
    except Exception:
        await game_input.fill(safe_home)
        await graphics_page.wait_for_timeout(400)
        try:
            fallback_opt = graphics_page.get_by_role("option", name=search_pattern).first
            await fallback_opt.wait_for(state="visible", timeout=2500)
            await fallback_opt.click(force=True)
            logger.info("Матч выбран по названию команд!")
        except Exception:
            rev_pattern = re.compile(f"{re.escape(safe_away)}.*{re.escape(safe_home)}", re.IGNORECASE)
            rev_opt = graphics_page.get_by_role("option", name=rev_pattern).first
            await rev_opt.wait_for(state="visible", timeout=2500)
            await rev_opt.click(force=True)
            logger.info("Матч выбран в обратном порядке!")

    # Ждем завершения загрузки изображений и логотипов команд в превью
    await graphics_page.evaluate("""async () => {
        const imgs = Array.from(document.querySelectorAll('img'));
        await Promise.all(imgs.map(img => {
            if (img.complete) return Promise.resolve();
            return new Promise(r => {
                img.onload = r;
                img.onerror = r;
                setTimeout(r, 2000);
            });
        }));
    }""")
    await graphics_page.wait_for_timeout(800)

    # 5. МГНОВЕННЫЙ ЗАХВАТ ОБЛОЖКИ (4K DOM-РЕНДЕР БЕЗ АРТЕФАКТОВ)
    logger.info("Генерация обложки...")

    # Способ 1: Прямой высококачественный локальный захват DOM-элемента (4500x2532px)
    # Скрываем верхнюю шапку и меню сайта, чтобы не накладывались артефакты при масштабировании
    try:
        prepare_script = """() => {
            document.querySelectorAll('header, nav, .mantine-Header-root, .mantine-Navbar-root, .mantine-AppShell-header, .mantine-AppShell-navbar, [class*="HeaderLayout_header"]').forEach(el => {
                el.style.display = 'none';
            });

            const container = document.querySelector('.IgraphicsStatsPage_imgContainer__Ll_Cs, [class*="imgContainer"]');
            let origTransform = '';
            if (container) {
                origTransform = container.style.transform;
                container.style.transform = 'none';
            }

            const tableWrapper = document.querySelector('.IgraphicsCover2Component_tableWrapper__Bwodp, [class*="tableWrapper"]');
            let origZ = '';
            if (tableWrapper) {
                origZ = tableWrapper.style.zIndex;
                tableWrapper.style.zIndex = '999999';
            }

            return { origTransform, origZ };
        }"""
        state = await graphics_page.evaluate(prepare_script)
        await graphics_page.wait_for_timeout(150)

        cover_locator = graphics_page.locator(".IgraphicsCover2Component_tableWrapper__Bwodp, [class*='tableWrapper']").first
        await cover_locator.screenshot(path=download_path)

        restore_script = """(state) => {
            document.querySelectorAll('header, nav, .mantine-Header-root, .mantine-Navbar-root, .mantine-AppShell-header, .mantine-AppShell-navbar, [class*="HeaderLayout_header"]').forEach(el => {
                el.style.display = '';
            });
            const container = document.querySelector('.IgraphicsStatsPage_imgContainer__Ll_Cs, [class*="imgContainer"]');
            if (container && state && state.origTransform !== undefined) {
                container.style.transform = state.origTransform;
            }
            const tableWrapper = document.querySelector('.IgraphicsCover2Component_tableWrapper__Bwodp, [class*="tableWrapper"]');
            if (tableWrapper && state && state.origZ !== undefined) {
                tableWrapper.style.zIndex = state.origZ;
            }
        }"""
        await graphics_page.evaluate(restore_script, state)

        if download_path.exists() and download_path.stat().st_size > 15000:
            logger.info(f"✅ Обложка успешно сохранена: {download_path}")
            return str(download_path)
    except Exception as local_err:
        logger.warning(f"Локальный захват не удался ({local_err}), пробуем сетевой перехват...")

    # Способ 2 (Запасной): Сетевой перехват через JS-хук кнопки DOWNLOAD IMAGE
    capture_script = """async () => {
        let capturedDataUrl = null;
        let capturedBlob = null;

        const origCreate = URL.createObjectURL;
        URL.createObjectURL = function(blob) {
            capturedBlob = blob;
            return origCreate.apply(this, arguments);
        };

        const origClick = HTMLAnchorElement.prototype.click;
        HTMLAnchorElement.prototype.click = function() {
            if (this.href) {
                capturedDataUrl = this.href;
            }
            return origClick.apply(this, arguments);
        };

        function getFiber(el) {
            const key = Object.keys(el).find(k => k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance'));
            return key ? el[key] : null;
        }

        const btns = Array.from(document.querySelectorAll('button'));
        const dl = btns.find(b => b.innerText.includes('DOWNLOAD IMAGE'));
        if (!dl) return { error: 'Кнопка DOWNLOAD IMAGE не найдена' };

        await new Promise(r => setTimeout(r, 400));

        let clickTriggered = false;
        try {
            const fiber = getFiber(dl);
            let curr = fiber;
            while (curr) {
                if (curr.memoizedProps && typeof curr.memoizedProps.onClick === 'function') {
                    curr.memoizedProps.onClick();
                    clickTriggered = true;
                    break;
                }
                curr = curr.return;
            }
        } catch (err) {}

        if (!clickTriggered) {
            dl.click();
        }

        for (let i = 0; i < 100; i++) {
            if (capturedBlob) {
                const reader = new FileReader();
                const b64Promise = new Promise(r => { reader.onloadend = () => r(reader.result); });
                reader.readAsDataURL(capturedBlob);
                return { success: true, b64: await b64Promise };
            }
            if (capturedDataUrl && (capturedDataUrl.startsWith('data:') || capturedDataUrl.startsWith('blob:'))) {
                return { success: true, b64: capturedDataUrl };
            }
            await new Promise(r => setTimeout(r, 100));
        }

        return { error: 'Таймаут ожидания рендера обложки' };
    }"""

    for render_attempt in range(1, 3):
        try:
            res = await graphics_page.evaluate(capture_script)
            if res and res.get("success") and res.get("b64"):
                b64_str = res["b64"].split(",", 1)[1]
                raw_bytes = base64.b64decode(b64_str)
                with open(download_path, "wb") as f:
                    f.write(raw_bytes)
                logger.info(f"Обложка успешно скачана: {download_path}")
                return str(download_path)
            elif res and res.get("error"):
                logger.warning(f"Попытка рендера {render_attempt}/2: {res.get('error')}")
        except Exception as render_err:
            logger.warning(f"Попытка рендера обложки {render_attempt}/2 ({render_err}), повтор...")
            await asyncio.sleep(1.0)

    # Способ 3 (Резервный): Playwright expect_download
    try:
        async with graphics_page.expect_download(timeout=6000) as download_info:
            await graphics_page.get_by_role("button", name="DOWNLOAD IMAGE").click(force=True)
        download = await download_info.value
        await download.save_as(download_path)
        logger.info(f"Обложка успешно скачана через expect_download: {download_path}")
        return str(download_path)
    except Exception as fallback_err:
        logger.error(f"Сбой резервного скачивания: {fallback_err}")
        raise RuntimeError(f"Не удалось получить файл обложки: {fallback_err}")


async def prepare_graphics(context, match: MatchMetadata, pattern_mode: str = "Автовыбор", league: str = "AFL Moscow 8x8",
                           default_color: int = 1, stadium_colors: Optional[Dict[str, str]] = None,
                           color_position: int = 1, pattern_position: int = 1) -> str:
    """
    Генерация обложки матча на сайте AFL Graphics.
    Если генерация не завершается за 35 сек, страница автоматически перезагружается и пробуется заново.
    """
    if stadium_colors is None:
        stadium_colors = {}

    graphics_page = None
    for p in context.pages:
        if "afl-graphics" in p.url:
            graphics_page = p
            break

    if not graphics_page:
        graphics_page = await context.new_page()
        await graphics_page.goto("https://afl-graphics.vercel.app/igraphics/video")
        await graphics_page.wait_for_load_state("domcontentloaded")
    else:
        await graphics_page.bring_to_front()

    download_dir = Path(os.getcwd()) / "covers"
    download_dir.mkdir(exist_ok=True)
    safe_home_fname = match.team_home.replace(" ", "_").replace('"', "")
    safe_away_fname = match.team_away.replace(" ", "_").replace('"', "")
    file_name = f"{safe_home_fname}_{safe_away_fname}_tour{match.tour_number}.png"
    download_path = download_dir / file_name

    max_graphics_attempts = 3
    for attempt in range(1, max_graphics_attempts + 1):
        try:
            logger.info(f"Подготовка страницы AFL Graphics (Попытка {attempt}/{max_graphics_attempts})...")
            # Расширенный таймаут 35 секунд на всю процедуру
            return await asyncio.wait_for(
                _run_graphics_flow(
                    graphics_page, match, pattern_mode, league,
                    default_color, stadium_colors, color_position, pattern_position,
                    download_path
                ),
                timeout=35.0
            )
        except (asyncio.TimeoutError, Exception) as e:
            if attempt < max_graphics_attempts:
                logger.warning(f"⚠️ Обложка не скачалась ({e}). Обновляем страницу AFL Graphics и пробуем заново...")
                try:
                    await graphics_page.reload()
                    await graphics_page.wait_for_load_state("domcontentloaded")
                    await graphics_page.wait_for_timeout(1000)
                except Exception as rel_err:
                    logger.warning(f"Ошибка при перезагрузке страницы: {rel_err}")
            else:
                logger.error(f"Не удалось сгенерировать обложку после {max_graphics_attempts} попыток: {e}")
                raise e
