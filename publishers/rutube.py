import logging
import asyncio
from pathlib import Path
from models import MatchMetadata

logger = logging.getLogger(__name__)


async def publish_stream(context, match_data: MatchMetadata, cover_path: str, description_text: str, keys_file: str) -> str:
    logger.info("Ищем вкладку Rutube Studio...")
    rutube_page = None

    for page in context.pages:
        if "studio.rutube.ru" in page.url:
            rutube_page = page
            break

    if not rutube_page:
        logger.warning("Вкладка Rutube не найдена (возможно, была закрыта). Открываем новую...")
        rutube_page = await context.new_page()
        await rutube_page.goto("https://studio.rutube.ru/streams")
        await rutube_page.wait_for_timeout(2000)
    else:
        logger.info("Вкладка Rutube найдена. Переключаемся...")
        await rutube_page.bring_to_front()

    try:
        # 1. ПЕРЕХОД К СОЗДАНИЮ ТРАНСЛЯЦИИ pashalka
        if "streams" not in rutube_page.url:
            await rutube_page.goto("https://studio.rutube.ru/streams")
            await rutube_page.wait_for_timeout(2000)

        logger.info("Нажимаем 'Добавить' (или '+') -> 'Начать трансляцию'...")
        try:
            # Универсальный локатор: ищем кнопку, внутри которой есть уникальная SVG-иконка плюса
            add_btn = rutube_page.locator("button:has(svg use[*|href='#IconDsMainPlus'])").first
            await add_btn.wait_for(state="visible", timeout=5000)
            await add_btn.click()
        except Exception as e:
            logger.warning(f"Не удалось найти кнопку по иконке, пробуем по тексту... ({e})")
            # Резервный вариант
            await rutube_page.get_by_role("button", name="Добавить", exact=True).click()

        await rutube_page.wait_for_timeout(1000)

        # Кликаем по выпадающему меню
        await rutube_page.get_by_role("menuitem", name="Начать трансляцию").click()
        await rutube_page.wait_for_timeout(1500)

        # 2. ЗАПОЛНЕНИЕ ОСНОВНЫХ ДАННЫХ
        logger.info(f"Вводим название: {match_data.stream_title}")
        title_input = rutube_page.get_by_role("textbox", name="Название")
        await title_input.click()
        await title_input.fill(match_data.stream_title)

        logger.info("Вводим описание из конфигурационного файла...")
        desc_input = rutube_page.get_by_role("textbox", name="Описание")
        await desc_input.fill(description_text)

        # 3. ВЫБОР КАТЕГОРИИ
        logger.info("Выбираем категорию 'Спорт'...")
        await rutube_page.get_by_text("Выберите категорию").click()
        await rutube_page.get_by_text("Спорт", exact=True).click()
        await rutube_page.wait_for_timeout(500)

        logger.info("Нажимаем 'Сохранить и продолжить'...")
        try:
            await rutube_page.get_by_role("button", name="Сохранить и продолжить").click(timeout=5000)
        except Exception:
            logger.info("Стандартная кнопка 'Сохранить и продолжить' не найдена, ищем по тексту...")
            await rutube_page.locator("text=Сохранить и продолжить").first.click()
        await rutube_page.wait_for_timeout(2000)

        # 4. ЗАГРУЗКА ОБЛОЖКИ (ВНУТРЕННИЙ ЦИКЛ С ПЕРЕЗАГРУЗКОЙ)
        cover_max_retries = 3
        cover_uploaded = False

        for cover_attempt in range(1, cover_max_retries + 1):
            logger.info(f"Загружаем обложку (Попытка {cover_attempt}/{cover_max_retries}): {cover_path}")

            try:
                # Если это 2-я или 3-я попытка, сбрасываем баги и заходим в редактирование заново
                if cover_attempt > 1:
                    logger.info("Перезагружаем страницу для сброса зависшего состояния...")
                    await rutube_page.reload()
                    await rutube_page.wait_for_timeout(5000)  # Ждем прогрузки страницы

                    logger.info("Нажимаем 'Редактировать' для повторной загрузки обложки...")
                    await rutube_page.get_by_role("button", name="Редактировать").click()
                    await rutube_page.wait_for_timeout(2000)

                # Нажимаем на иконку карандаша для изменения обложки (или старую кнопку "Изменить")
                async with rutube_page.expect_file_chooser() as fc_info:
                    try:
                        pencil_icon = rutube_page.locator("svg:has(use[*|href='#IconDsEditorPencil'])").first
                        await pencil_icon.click(timeout=3000)
                    except Exception:
                        logger.info("Иконка карандаша не найдена, пробуем старую кнопку 'Изменить'...")
                        await rutube_page.get_by_role("button", name="Изменить").click()

                file_chooser = await fc_info.value
                await file_chooser.set_files(cover_path)
                await rutube_page.wait_for_timeout(1500)

                logger.info("Нажимаем 'Готово' и страхуемся от зависания Rutube...")
                await rutube_page.get_by_role("button", name="Готово").click(timeout=5000)
                await rutube_page.wait_for_timeout(3000)

                # Если кнопка "Готово" всё еще торчит на экране, значит модалка зависла
                if await rutube_page.get_by_role("button", name="Готово").is_visible():
                    logger.warning("Rutube бесконечно грузит превью. Сбрасываем окно через Escape...")
                    await rutube_page.keyboard.press("Escape")
                    await rutube_page.wait_for_timeout(1000)

                    if cover_attempt == cover_max_retries:
                        raise RuntimeError("Rutube намертво завис на сохранении обложки после 3 попыток")
                    else:
                        logger.info("Пробуем загрузить картинку в этот же черновик еще раз...")
                else:
                    cover_uploaded = True
                    break  # Успех, выходим из цикла!

            except Exception as e:
                logger.warning(f"Окно обложки повело себя нестандартно. Жмем Escape... ({e})")
                await rutube_page.keyboard.press("Escape")
                await rutube_page.wait_for_timeout(1000)
                if cover_attempt == cover_max_retries:
                    raise Exception("Сбой модального окна обложки")

        # Если успешно загрузили, идем сохранять (если окно не закрылось само)
        if cover_uploaded:
            logger.info("Проверяем кнопку 'Сохранить'...")
            try:
                save_btn = rutube_page.get_by_role("button", name="Сохранить")
                if await save_btn.is_visible(timeout=3000):
                    await save_btn.click()
                    logger.info("Трансляция сохранена.")
                else:
                    logger.info("Окно закрылось автоматически, идем дальше.")
            except Exception:
                logger.warning("Ошибка при поиске кнопки 'Сохранить'. Идем дальше.")

        await rutube_page.wait_for_timeout(2000)

        # 5. АВТОСТАРТ ЧЕРЕЗ РЕДАКТИРОВАНИЕ
        logger.info("Открываем настройки трансляции ('Редактировать')...")
        await rutube_page.get_by_role("button", name="Редактировать").click()
        await rutube_page.wait_for_timeout(1500)

        logger.info("Включаем Автоматический запуск...")
        try:
            autostart_checkbox = rutube_page.locator("input[name='autoStart']")
            await autostart_checkbox.click(force=True, timeout=3000)
        except Exception:
            autostart_checkbox = rutube_page.locator("div[class*='autoStart__checkbox']").first
            await autostart_checkbox.scroll_into_view_if_needed()
            await autostart_checkbox.click(force=True)

        await rutube_page.wait_for_timeout(500)

        logger.info("Сохраняем изменения трансляции...")
        await rutube_page.get_by_role("button", name="Сохранить").click()
        await rutube_page.wait_for_timeout(2000)

        # 6. СБОР ДАННЫХ СО СТРАНИЦЫ ПРЕДПРОСМОТРА
        logger.info("Ждем загрузки страницы предпросмотра...")
        share_btn = rutube_page.locator("button:has(svg use[*|href='#IconDsMainShare'])").first
        await share_btn.wait_for(state="visible", timeout=15000)
        await rutube_page.wait_for_timeout(1000)

        logger.info("Нажимаем 'Поделиться' для получения ссылки на видео...")
        await share_btn.click()
        await rutube_page.wait_for_timeout(1000)

        video_url = ""
        server_url = ""
        stream_key = ""

        popup = rutube_page.locator("div[role='dialog']")
        await popup.wait_for(state="visible", timeout=5000)

        popup_input = popup.locator("input").first
        if await popup_input.count() > 0:
            val = await popup_input.input_value()
            if val and "rutube.ru/video/" in val:
                video_url = val
                logger.info(f"Ссылка на видео найдена: {video_url}")

        logger.info("Закрываем окно 'Поделиться'...")
        close_popup_btn = rutube_page.locator("button[aria-label='Close Popup']")
        if await close_popup_btn.count() > 0:
            await close_popup_btn.click()
        else:
            await rutube_page.keyboard.press("Escape")
        await rutube_page.wait_for_timeout(1000)

        logger.info("Собираем ключи трансляции...")
        all_inputs = await rutube_page.locator("input").all()
        for inp in all_inputs:
            try:
                val = await inp.input_value()
                inp_type = await inp.get_attribute("type")

                if val and (val.startswith("rtmp://") or val.startswith("rtmps://")):
                    server_url = val
                elif inp_type == "password" and val:
                    stream_key = val
            except:
                pass

        if not server_url or not stream_key:
            copy_buttons = rutube_page.locator("button:has(svg use[*|href='#IconDsMainCopy'])")
            if await copy_buttons.count() >= 2:
                await copy_buttons.nth(0).click()
                await rutube_page.wait_for_timeout(500)
                try:
                    clip1 = await rutube_page.evaluate("navigator.clipboard.readText()")
                    if clip1 and not clip1.startswith("rtmp"):
                        stream_key = clip1
                except:
                    pass

                await copy_buttons.nth(1).click()
                await rutube_page.wait_for_timeout(500)
                try:
                    clip2 = await rutube_page.evaluate("navigator.clipboard.readText()")
                    if clip2 and clip2.startswith("rtmp"):
                        server_url = clip2
                except:
                    pass

        # Просто удаляем строчку keys_file = ..., так как переменная теперь приходит извне
        with open(keys_file, "a", encoding="utf-8") as f:
            f.write(f"Матч: {match_data.stream_title}\n")
            f.write(f"URL видео: {video_url}\n")
            f.write(f"Сервер: {server_url}\n")
            f.write(f"Ключ: {stream_key}\n")
            f.write(f"Лого хозяев: {match_data.logo_home}\n")
            f.write(f"Лого гостей: {match_data.logo_away}\n")
            f.write(f"Сокр. хозяев: {match_data.abbr_home}\n")
            f.write(f"Сокр. гостей: {match_data.abbr_away}\n")
            f.write("-" * 50 + "\n")

        logger.info(f"Данные успешно записаны в файл: {keys_file}")
        await rutube_page.goto("https://studio.rutube.ru/streams")
        await rutube_page.wait_for_timeout(2000)

        return video_url

    except Exception as e:
        logger.error(f"Ошибка при публикации на Rutube: {e}", exc_info=True)
        await rutube_page.screenshot(path="rutube_error_screenshot.png")
        logger.info("Сохранен скриншот ошибки: rutube_error_screenshot.png")
        try:
            await rutube_page.goto("https://studio.rutube.ru/streams")
        except:
            pass
        raise e
