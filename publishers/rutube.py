import logging
import asyncio
from pathlib import Path
from models import MatchMetadata

logger = logging.getLogger(__name__)

# Шаблон описания трансляции
AFL_DESCRIPTION = """Заявляйся в AFL!

+7 (915) 296-80-45
https://vk.com/s.lebedev24

Телеграм AFL — https://t.me/aflrussiа

AFL VK – https://vk.com/aflmoscow

Instagram* AFL – платформа запрещена на территории РФ https://instagram.com/afl_russia

Приложение AFL:

Iphone — https://apps.apple.com/ru/app/afl/id1555695558

Android — https://play.google.com/store/apps/details?id=com.foo"""
# бесит)

async def publish_stream(context, match_data: MatchMetadata, cover_path: str) -> None:
    logger.info("Ищем вкладку Rutube Studio...")
    rutube_page = None

    for page in context.pages:
        if "studio.rutube.ru" in page.url:
            rutube_page = page
            break

    if not rutube_page:
        raise Exception("Открой вкладку Rutube Studio (https://studio.rutube.ru/streams) в браузере!")

    logger.info("Вкладка Rutube найдена. Переключаемся...")
    await rutube_page.bring_to_front()

    try:
        # 1. ПЕРЕХОД К СОЗДАНИЮ ТРАНСЛЯЦИИ
        if "streams" not in rutube_page.url:
            await rutube_page.goto("https://studio.rutube.ru/streams")
            await rutube_page.wait_for_timeout(2000)

        logger.info("Нажимаем 'Добавить' -> 'Начать трансляцию'...")
        await rutube_page.get_by_role("button", name="Добавить").click()
        await rutube_page.get_by_role("menuitem", name="Начать трансляцию").click()
        await rutube_page.wait_for_timeout(1500)

        # 2. ЗАПОЛНЕНИЕ ОСНОВНЫХ ДАННЫХ
        logger.info(f"Вводим название: {match_data.stream_title}")
        title_input = rutube_page.get_by_role("textbox", name="Название")
        await title_input.click()
        # Очищаем поле
        await title_input.fill(match_data.stream_title)

        logger.info("Вводим описание...")
        desc_input = rutube_page.get_by_role("textbox", name="Описание")
        await desc_input.fill(AFL_DESCRIPTION)

        # 3. ВЫБОР КАТЕГОРИИ
        logger.info("Выбираем категорию 'Спорт'...")
        await rutube_page.get_by_text("Выберите категорию").click()
        await rutube_page.get_by_text("Спорт", exact=True).click()
        await rutube_page.wait_for_timeout(500)

        logger.info("Нажимаем 'Сохранить и продолжить'...")
        await rutube_page.get_by_role("button", name="Сохранить и продолжить").click()

        # Ждем прогрузки следующего шага
        await rutube_page.wait_for_timeout(2000)

        # 4. ЗАГРУЗКА ОБЛОЖКИ
        logger.info(f"Загружаем обложку: {cover_path}")

        # Правильный паттерн Playwright для загрузки файлов через кнопку
        async with rutube_page.expect_file_chooser() as fc_info:
            await rutube_page.get_by_role("button", name="Изменить").click()

        file_chooser = await fc_info.value
        await file_chooser.set_files(cover_path)

        await rutube_page.wait_for_timeout(1000)

        # Подтверждаем загрузку картинки
        await rutube_page.get_by_role("button", name="Готово").click()
        await rutube_page.wait_for_timeout(2000)

        # 5. АВТОСТАРТ И СОХРАНЕНИЕ
        logger.info("Включаем Автостарт...")
        autostart_checkbox = rutube_page.locator("div[class*='autoStart__checkbox']").first
        await autostart_checkbox.scroll_into_view_if_needed()
        await autostart_checkbox.click()
        await rutube_page.wait_for_timeout(500)

        logger.info("Сохраняем трансляцию...")
        await rutube_page.get_by_role("button", name="Сохранить").click()

        # 6. СБОР ДАННЫХ СО СТРАНИЦЫ ПРЕДПРОСМОТРА
        logger.info("Ждем загрузки страницы предпросмотра...")

        # Ждем появления кнопки "Поделиться" (ищем по уникальной иконке, чтобы работало на любых экранах)
        share_btn = rutube_page.locator("button:has(svg use[*|href='#IconDsMainShare'])").first
        await share_btn.wait_for(state="visible", timeout=15000)
        await rutube_page.wait_for_timeout(1000)

        logger.info("Нажимаем 'Поделиться' для получения ссылки на видео...")
        await share_btn.click()
        await rutube_page.wait_for_timeout(1000)

        video_url = ""
        server_url = ""
        stream_key = ""

        # 6.1 Ищем ссылку на видео внутри открытого попапа
        popup = rutube_page.locator("div[role='dialog']")
        await popup.wait_for(state="visible", timeout=5000)

        # Инпут со ссылкой лежит в блоке с меткой "Ссылка" (как видно в дампе)
        popup_input = popup.locator("input").first
        if await popup_input.count() > 0:
            val = await popup_input.input_value()
            if val and "rutube.ru/video/" in val:
                video_url = val
                logger.info(f"🔗 Ссылка на видео найдена: {video_url}")

        # Закрываем модалку через специальную кнопку крестика (надежнее, чем Escape)
        logger.info("Закрываем окно 'Поделиться'...")
        close_popup_btn = rutube_page.locator("button[aria-label='Close Popup']")
        if await close_popup_btn.count() > 0:
            await close_popup_btn.click()
        else:
            await rutube_page.keyboard.press("Escape")
        await rutube_page.wait_for_timeout(1000)

        # 6.2 Получаем Server URL и Stream Key с основной страницы
        logger.info("Собираем ключи трансляции...")
        # Умный поиск: пробегаемся по всем полям ввода на странице
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

        # Запасной план для ключей (через кнопки копирования)
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

        # 7. СОХРАНЕНИЕ В TXT ФАЙЛ
        keys_file = "stream_keys.txt"
        with open(keys_file, "a", encoding="utf-8") as f:
            f.write(f"Матч: {match_data.stream_title}\n")
            f.write(f"URL видео: {video_url}\n")
            f.write(f"Сервер: {server_url}\n")
            f.write(f"Ключ: {stream_key}\n")
            f.write("-" * 50 + "\n")

        logger.info(f"💾 Данные успешно записаны в файл: {keys_file}")

        # Возвращаемся к списку трансляций
        await rutube_page.goto("https://studio.rutube.ru/streams")
        await rutube_page.wait_for_timeout(2000)

        return video_url

    except Exception as e:

        logger.error(f"Ошибка при публикации на Rutube: {e}", exc_info=True)
        await rutube_page.screenshot(path="rutube_error_screenshot.png")
        logger.info("📸 Сохранен скриншот ошибки: rutube_error_screenshot.png")

        # Пытаемся вернуться на главную страницу студии при ошибке
        try:
            await rutube_page.goto("https://studio.rutube.ru/streams")
        except:
            pass
        raise e
