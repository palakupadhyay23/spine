package app

actual class Clock actual constructor() {
    actual fun now(): Long = 1L
}

actual fun platformName(): String = "ios"
