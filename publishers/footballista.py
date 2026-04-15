import logging

logger = logging.getLogger(__name__)


async def add_video_link_to_match(context, match_url: str, video_url: str) -> None:
    logger.info(f"Вставляем видео ({video_url}) на страницу: {match_url}")

    # Открываем новую вкладку, чтобы не сбивать логику других окон
    page = await context.new_page()

    try:
        await page.goto(match_url)
        await page.wait_for_timeout(2000)

        # Нажимаем кнопку "Добавить видео"
        await page.locator('button', has_text="Добавить видео").click()
        await page.wait_for_timeout(500)

        # Вставляем ссылку в поле
        await page.get_by_placeholder("Youtube, Rutube or VK").fill(video_url)
        await page.wait_for_timeout(500)

        # Нажимаем кнопку "добавить" в модальном окне
        # Ищем строго кнопку с текстом 'добавить' внутри блока .buttons-row.small
        # Блять псевдокласс :text-is ищет строгое совпадение, а .first берет первую, чтобы избежать дублей
        add_btn = page.locator('button:text-is("добавить")').first
        await add_btn.click()

        # Ждем, чтобы сайт успел сохранить запрос
        await page.wait_for_timeout(1500)
        logger.info("✅ Видео успешно прикреплено на сайте Footballista!")

    except Exception as e:
        logger.error(f"❌ Ошибка при добавлении видео на сайт: {e}")
        await page.screenshot(path="footballista_link_error.png")
    finally:
        # Обязательно закрываем вкладку, чтобы они не плодились
        await page.close()
