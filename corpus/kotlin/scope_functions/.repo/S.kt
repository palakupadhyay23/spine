package app.ui

import androidx.compose.ui.Modifier
import java.io.File

class Screen(private val m: Modifier, private val f: File) {
    fun draw() {
        m.let { }
        m.run { }
        m.takeIf { true }
        f.use { }
        m.also { }
    }
}
